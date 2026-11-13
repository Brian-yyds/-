"""Hourly read-only integrity/lag audit and verified daily SQLite backups."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
import json,os,shutil,sqlite3,time
from pathlib import Path

BASE=Path('/opt/perp-workbench')
ZONE=timezone(timedelta(hours=8))
DBS={'workbench':Path('user_data/workbench.sqlite'),'delivery':Path('telegram/delivery.sqlite')}


def readonly(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)


def integrity(path,seconds=20):
    deadline=time.monotonic()+seconds
    try:
        with closing(readonly(path)) as con:
            con.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
            return 'ok' if con.execute('PRAGMA integrity_check(1)').fetchall()==[('ok',)] else 'integrity_failed'
    except sqlite3.OperationalError:return 'check_unavailable'
    except (sqlite3.DatabaseError,OSError):return 'integrity_failed'


def daily_backup(source,directory,name,now):
    """Only replace a new completed backup after it passes full integrity."""
    day=datetime.fromtimestamp(now,ZONE).strftime('%Y%m%d')
    target=directory/(name+'-'+day+'.sqlite')
    if target.exists():
        if integrity(target)!='ok':raise ValueError('existing_backup_integrity_failed')
        return dict(status='already_saved',path=target.name)
    temp=directory/(name+'-'+day+'.tmp')
    assert target.resolve().parent==directory.resolve() and temp.resolve().parent==directory.resolve()
    deadline=time.monotonic()+45
    def progress(*_):
        if time.monotonic()>deadline:raise TimeoutError('backup_deadline')
    try:
        with closing(readonly(source)) as src,closing(sqlite3.connect(temp)) as dst:
            src.backup(dst,pages=256,progress=progress,sleep=.05)
        if integrity(temp)!='ok':raise ValueError('backup_integrity_failed')
        temp.chmod(0o600);temp.replace(target)
        # Exact owned backup filename patterns only; never delete source data.
        files=sorted(directory.glob(name+'-????????.sqlite'),reverse=True)
        for old in files[7:]:
            if old.is_file() and old.resolve().parent==directory.resolve():old.unlink()
        return dict(status='saved',path=target.name,bytes=target.stat().st_size)
    finally:
        if temp.exists():temp.unlink()


def pipeline(path,now):
    with closing(readonly(path)) as con:
        pending=con.execute('''SELECT COUNT(DISTINCT w.chain||':'||w.address) FROM gmgn_watch w
          JOIN gmgn_alarm_evidence g ON g.chain=w.chain AND g.address=w.address
          JOIN wallet_signal_events e ON e.chain=w.chain AND e.address=w.address
          WHERE w.first_alert<=? AND w.expires_at>? AND e.last_observed>=? AND e.first_observed<=?
          AND NOT EXISTS(SELECT 1 FROM special_signal_usage u WHERE u.chain=e.chain AND u.signal_id=e.signal_id)''',
          (now,now,now-10*86400,now)).fetchone()[0]
        delays=[]
        for triggered,raw in con.execute('SELECT triggered,data FROM special_alerts ORDER BY id DESC LIMIT 50'):
            item=json.loads(raw)
            if item.get('migration_recovered'):continue
            first=item.get('web3_first_observed');gmgn=item.get('gmgn_triggered')
            if isinstance(first,(int,float)) and isinstance(gmgn,(int,float)):
                delays.append(max(0,triggered-max(first,gmgn)))
        return dict(qualified_pairs_pending=pending,recent_match_delay_max_seconds=round(max(delays,default=0),3),samples=len(delays))


def run():
    import fcntl
    os.umask(0o077)
    assert (BASE/'.deployment-owner').read_text().strip()=='codex-perp-workbench-v1'
    state=BASE/'shared/maintenance';state.mkdir(mode=0o700,exist_ok=True)
    with (state/'maintenance.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        now=time.time();disk=shutil.disk_usage(BASE)
        report=dict(checked_at=now,disk_free=disk.free,disk_critical=disk.free<200*1024*1024,integrity={},backups={},notes=[])
        directory=BASE/'shared/backups';directory.mkdir(mode=0o700,exist_ok=True)
        for name,relative in DBS.items():
            source=BASE/'shared'/relative;status=integrity(source);report['integrity'][name]=status
            if status=='ok' and not report['disk_critical']:
                try:report['backups'][name]=daily_backup(source,directory,name,now)
                except Exception:report['backups'][name]=dict(status='backup_failed')
        report['database_integrity']='ok' if all(v=='ok' for v in report['integrity'].values()) else 'requires_review'
        try:report['pipeline']=pipeline(BASE/'shared/user_data/workbench.sqlite',now)
        except (OSError,sqlite3.Error,ValueError,KeyError):report['notes'].append('pipeline_inspection_failed')
        if report.get('pipeline',{}).get('qualified_pairs_pending'):report['notes'].append('qualified_matches_pending')
        report['wal_bytes']={n:Path(str(BASE/'shared'/r)+'-wal').stat().st_size if Path(str(BASE/'shared'/r)+'-wal').exists() else 0 for n,r in DBS.items()}
        if any(s>256*1024*1024 for s in report['wal_bytes'].values()):report['notes'].append('wal_large_requires_review')
        report['finished_at']=time.time()
        temp=state/'status.tmp';temp.write_text(json.dumps(report,ensure_ascii=True));temp.replace(state/'status.json')
        print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':run()
