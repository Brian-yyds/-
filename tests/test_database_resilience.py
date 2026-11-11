from pathlib import Path
import sys,sqlite3
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store
import market_scanner as scanner


def test_transient_open_io_error_retries_before_writing_and_closes(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'retry.sqlite')
    connect=sqlite3.connect;opened=[]
    class FirstConnection:
        def __init__(self,con):self.con=con
        def execute(self,sql):raise sqlite3.OperationalError('disk I/O error')
        def close(self):self.con.close()
    def factory(*a,**k):
        con=connect(*a,**k);opened.append(con)
        return FirstConnection(con) if len(opened)==1 else con
    monkeypatch.setattr(store.sqlite3,'connect',factory)
    monkeypatch.setattr(store.time,'sleep',lambda _:None)
    store.put('once',{'count':1})
    assert store.get('once')=={'count':1}
    with pytest.raises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')


def test_schema_initialization_failure_closes_connection_without_retry(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'bad.sqlite')
    calls=[]
    class Connection:
        def execute(self,sql):
            calls.append(sql)
            if sql.startswith('CREATE'):raise sqlite3.DatabaseError('database disk image is malformed')
        def close(self):calls.append('closed')
    monkeypatch.setattr(store.sqlite3,'connect',lambda *a,**k:Connection())
    with pytest.raises(sqlite3.DatabaseError):
        with store.connect():pytest.fail('unusable connection yielded')
    assert calls.count('closed')==1 and sum(s.startswith('CREATE') for s in calls)==1


def test_nested_market_thread_recovers_from_report_io_error(monkeypatch,capsys):
    monitor=SimpleNamespace(blocked_until=0)
    market=object.__new__(scanner.MarketScanner);market.monitor=monitor
    reports=[];sleeps=[]
    class Pool:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
    def report(*args):
        reports.append(1)
        if len(reports)==1:raise sqlite3.OperationalError('disk I/O error')
    def sleep(*args):
        sleeps.append(1)
        if len(sleeps)==2:raise StopIteration
    monkeypatch.setattr(scanner,'ThreadPoolExecutor',Pool)
    monkeypatch.setattr(scanner.store,'settings',lambda:{'watchlist':[]})
    monkeypatch.setattr(scanner.monitoring,'market_symbols',lambda _:[])
    monkeypatch.setattr(scanner.time,'sleep',sleep)
    monkeypatch.setattr(market,'report',report)
    with pytest.raises(StopIteration):market.run()
    assert len(reports)==2 and 'market_scanner_retry' in capsys.readouterr().out
