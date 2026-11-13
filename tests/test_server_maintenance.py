import sqlite3
from pathlib import Path
import pytest
from deploy import server_maintenance as m


def database(path):
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE events(id PRIMARY KEY,data)');con.execute('INSERT INTO events VALUES(1,?)',('original',))


def test_verified_snapshot_preserves_source_and_daily_dedup(tmp_path):
    src=tmp_path/'source.sqlite';database(src);dest=tmp_path/'backups';dest.mkdir()
    original=src.read_bytes();first=m.daily_backup(src,dest,'workbench',1794400000)
    assert first['status']=='saved' and src.read_bytes()==original
    assert m.integrity(dest/first['path'])=='ok'
    assert m.daily_backup(src,dest,'workbench',1794400001)['status']=='already_saved'
    assert len(list(dest.glob('*.sqlite')))==1


def test_failed_integrity_never_promotes_or_changes_source(tmp_path,monkeypatch):
    src=tmp_path/'source.sqlite';database(src);dest=tmp_path/'backups';dest.mkdir();original=src.read_bytes()
    monkeypatch.setattr(m,'integrity',lambda *_:'integrity_failed')
    with pytest.raises(ValueError):m.daily_backup(src,dest,'workbench',1794400000)
    assert not list(dest.iterdir()) and src.read_bytes()==original


def test_seven_backups_retained_without_touching_unrelated_files(tmp_path):
    src=tmp_path/'source.sqlite';database(src);dest=tmp_path/'backups';dest.mkdir()
    other=dest/'unrelated.sqlite';other.write_text('keep')
    for i in range(9):m.daily_backup(src,dest,'workbench',1794400000+i*86400)
    assert len(list(dest.glob('workbench-*.sqlite')))==7 and other.read_text()=='keep'


def test_corrupt_database_is_reported_not_recreated(tmp_path):
    p=tmp_path/'bad.sqlite';p.write_bytes(b'not a sqlite database')
    assert m.integrity(p)=='integrity_failed' and p.read_bytes()==b'not a sqlite database'
    missing=tmp_path/'missing.sqlite';assert m.integrity(missing)!='ok' and not missing.exists()


def test_existing_corrupt_backup_never_gets_a_healthy_status(tmp_path):
    src=tmp_path/'source.sqlite';database(src);dest=tmp_path/'backups';dest.mkdir()
    first=m.daily_backup(src,dest,'workbench',1794400000);saved=dest/first['path'];saved.write_bytes(b'corrupted backup')
    with pytest.raises(ValueError):m.daily_backup(src,dest,'workbench',1794400001)
    assert saved.read_bytes()==b'corrupted backup' and m.integrity(src)=='ok'
