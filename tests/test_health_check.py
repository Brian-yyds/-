"""No external API calls, real process termination or live database writes."""
from types import SimpleNamespace
import pytest
import store
import health_check as h
import service_supervisor as s

NOW=2000000.


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'audit.sqlite')


def healthy():
    values={key:{'heartbeat':NOW} for key,_ in h.HEARTBEATS.values()}
    values.update(market_health={'heartbeat':NOW},sync_config={'enabled':False},signal_dashboard={'generated_at':NOW})
    values['gmgn_market']['chains']={c:{'updated':NOW} for c in ('sol','bsc','base','robinhood')}
    values['web3_signal_health']['chains']={c:{'updated':NOW} for c in ('sol','bsc')}
    runtime={'services':{name:dict(pid=i,created_at=NOW-2000,started_at=NOW-2000)
                         for i,name in enumerate(h.SERVICES,10)}}
    return values,runtime


def inspect(values,runtime,**kwargs):
    return {r['service']:r for r in h.check(values,runtime,NOW,kwargs.get('alive',lambda *_:True),kwargs.get('web_ok',True))}


def test_no_alerts_does_not_mean_fault_paused_upload_remains_paused():
    values,runtime=healthy();report=inspect(values,runtime)
    assert not any(r['repairable'] for r in report.values())
    assert report['export']['code']=='local_only_upload_paused'
    assert values['sync_config']['enabled'] is False


def test_stale_data_versus_paused_cooldown_and_source_errors():
    values,runtime=healthy();values['gmgn_market']['heartbeat']=NOW-1000
    assert inspect(values,runtime)['gmgn']['repairable']
    values['gmgn_rate_limit_until']=NOW+3600
    assert inspect(values,runtime)['gmgn']['code']=='provider_backoff'
    values['gmgn_config']={'enabled':False}
    assert inspect(values,runtime,alive=lambda *_:False)['gmgn']['code']=='paused'
    values['web3_signal_health']['chains']['sol']={'updated':NOW-1000,'error':'timeout'}
    assert inspect(values,runtime)['signals']['code']=='source_delayed:sol'
    assert not inspect(values,runtime)['signals']['repairable']
    # News polls every 15m, but the worker has its own heartbeat; do not fetch again.
    values['blockbeats_health'].update(last_success=NOW-899,next_poll_at=NOW+1)
    assert inspect(values,runtime)['news']['code']=='ok'


def test_missing_heartbeat_bad_local_page_and_startup_grace():
    values,runtime=healthy();values['monitor_health']={}
    assert inspect(values,runtime)['collector']['repairable']
    assert inspect(values,runtime,web_ok=False)['workbench']['repairable']
    runtime['services']['collector']['started_at']=NOW-299
    assert inspect(values,runtime)['collector']['code']=='starting'
    values['signal_dashboard']['generated_at']=NOW-301
    assert inspect(values,runtime)['signals']['code']=='special_snapshot_stalled'
    values['web3_signal_health']['heartbeat']=float('nan')
    assert inspect(values,runtime)['signals']['repairable']


def test_process_identity_checks_creation_time_and_exact_script(monkeypatch):
    proc=SimpleNamespace(create_time=lambda:123.,cmdline=lambda:['python',str(store.ROOT/'gmgn_monitor.py')])
    monkeypatch.setattr(h.psutil,'Process',lambda _:proc)
    assert h.owned(1,123,'gmgn_monitor.py')
    assert not h.owned(1,124,'gmgn_monitor.py')
    assert not h.owned(1,123,'app.py')
    proc.cmdline=lambda:['python',str(store.ROOT/'gmgn_monitor.py.bak')]
    assert not h.owned(1,123,'gmgn_monitor.py')


def test_live_restart_needs_two_observations_then_checks_recovery(isolated,monkeypatch):
    supervisor=s.Supervisor({});supervisor.children={'gmgn':object()}
    reports=[];repairs=[]
    monkeypatch.setattr(h,'inspect',lambda runtime,now:dict(checked_at=now,findings=[dict(service='gmgn',code='heartbeat_stalled',repairable=True)]))
    monkeypatch.setattr(h,'save_report',reports.append)
    monkeypatch.setattr(supervisor,'restart',lambda name,now:repairs.append((name,now)) or 'restarted_pending_health')
    supervisor.audit(NOW)
    assert not repairs and supervisor.next_audit==NOW+30
    supervisor.audit(NOW+30)
    assert repairs==[('gmgn',NOW+30)] and supervisor.next_audit==NOW+30+300
    assert reports[-1]['actions'][0]['result']=='restarted_pending_health'


def test_bounded_restart_does_not_touch_other_children_or_settings(isolated,monkeypatch):
    supervisor=s.Supervisor({});calls=[]
    monkeypatch.setattr(supervisor,'stopping',lambda:False)
    monkeypatch.setattr(supervisor,'stop_child',lambda name:calls.append(('stop',name)) or True)
    monkeypatch.setattr(supervisor,'spawn',lambda name:calls.append(('spawn',name)))
    store.put('sync_config',{'enabled':False});store.put('gmgn_config',{'graduate_volume':200000})
    assert supervisor.restart('gmgn',NOW)=='restarted_pending_health'
    assert supervisor.restart('gmgn',NOW+1)=='restart_budget_wait'
    assert supervisor.restart('gmgn',NOW+60)=='restarted_pending_health'
    assert supervisor.restart('gmgn',NOW+360)=='restarted_pending_health'
    assert supervisor.restart('gmgn',NOW+1200)=='restart_budget_wait'
    assert len(calls)==6 and all(name=='gmgn' for _,name in calls)
    assert store.get('sync_config')['enabled'] is False
    assert store.get('gmgn_config')['graduate_volume']==200000
    monkeypatch.setattr(supervisor,'stopping',lambda:True)
    assert supervisor.restart('gmgn',NOW+4000)=='stopping_no_restart'
    assert len(calls)==6


def test_database_error_never_deletes_or_repairs_database(isolated,monkeypatch):
    store.DB.write_text('corrupt fixture',encoding='utf-8')
    report=h.inspect({'services':{}},NOW)
    assert report['findings']==[dict(service='database',code='read_failed_no_changes',repairable=False)]
    h.save_report(report)
    assert store.DB.read_text()=='corrupt fixture'
    assert (store.USER/'health-audit.jsonl').exists()


def test_real_temporary_worker_exit_recovers_without_touching_live_services(isolated,monkeypatch):
    import sys
    import time
    monkeypatch.setattr(store,'ROOT',store.USER)
    script=store.USER/'gmgn_monitor.py'
    script.write_text("from pathlib import Path\nimport time\np=Path(__file__).with_suffix('.marker')\n"
                      "if not p.exists():\n p.touch()\n raise SystemExit(1)\n"
                      "while True: time.sleep(1)\n",encoding='utf-8')
    store.put('sync_config',{'enabled':False})
    supervisor=s.Supervisor({'gmgn':[sys.executable,str(script)]})
    try:
        supervisor.start();first=supervisor.children['gmgn'];first.wait(timeout=10)
        assert supervisor.tick()
        second=supervisor.children['gmgn']
        assert second.pid!=first.pid and second.poll() is None
        assert h.read_runtime()['services']['gmgn']['pid']==second.pid
        assert store.get('health_audit')['actions'][0]['result']=='restarted_pending_health'
        assert store.get('sync_config')['enabled'] is False
    finally:supervisor.close()
    assert second.poll() is not None
