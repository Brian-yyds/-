"""Local-only hourly inspection. --repair queues bounded supervisor repairs."""
import argparse
import json
import math
import sqlite3
import time
from pathlib import Path
import psutil
import store

INTERVAL=3600
SERVICES={'collector':'live_collector.py','gmgn':'gmgn_monitor.py','signals':'web3_signals.py',
          'news':'blockbeats_monitor.py','export':'export_worker.py','workbench':'app.py'}
HEARTBEATS={'collector':('monitor_health',300),'gmgn':('gmgn_market',600),
            'signals':('web3_signal_health',300),'news':('blockbeats_health',300),'export':('sync_health',300)}


def numeric(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else 0.


def snapshot():
    # Read-only: a missing/corrupt DB is reported, never recreated or deleted.
    with sqlite3.connect(store.DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as con:
        return {key:json.loads(value) for key,value in con.execute('SELECT key,value FROM kv')}


def owned(pid,created,script):
    try:
        process=psutil.Process(pid)
        target=(store.ROOT/script).resolve()
        return abs(process.create_time()-created)<.01 and any(
            Path(arg).is_absolute() and Path(arg).resolve()==target for arg in process.cmdline())
    except (psutil.Error,OSError,ValueError,TypeError):return False


def read_runtime():
    try:return json.loads((store.USER/'runtime.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def check(values,runtime,now,alive,web_ok=True):
    """Pure policy: never infer failure from an absence of alerts."""
    findings=[]
    def add(service,code,repair=False):findings.append(dict(service=service,code=code,repairable=repair))
    for name in SERVICES:
        metadata=runtime.get('services',{}).get(name,{})
        if not metadata:
            add(name,'not_managed');continue
        cfg_key={'gmgn':'gmgn_config','signals':'web3_signal_config','news':'blockbeats_config'}.get(name)
        if cfg_key and values.get(cfg_key,{}).get('enabled') is False:
            add(name,'paused');continue
        if not alive(name,metadata):
            add(name,'process_exited',True);continue
        age=now-numeric(metadata.get('started_at'))
        if age<300:
            add(name,'starting');continue
        if name=='workbench':
            add(name,'ok' if web_ok else 'local_page_unresponsive',not web_ok);continue
        key,limit=HEARTBEATS[name];health=values.get(key,{})
        if name=='gmgn':
            limit=max(limit,2*numeric(values.get('gmgn_config',{}).get('poll_seconds',60))+180)
            if numeric(values.get('gmgn_rate_limit_until'))>now or numeric(health.get('retry_at'))>now:
                add(name,'provider_backoff');continue
        if name=='collector' and numeric(health.get('blocked_until'))+120>now:
            add(name,'provider_backoff');continue
        heartbeat=numeric(health.get('heartbeat'))
        if not heartbeat or now-heartbeat>limit:
            add(name,'heartbeat_stalled',True);continue
        if name=='collector':
            market=numeric(values.get('market_health',{}).get('heartbeat'))
            if age>900 and (not market or now-market>900):
                add(name,'market_scanner_stalled',True);continue
        if name in ('gmgn','signals'):
            chains=health.get('chains',{})
            expected=('sol','bsc','base','robinhood') if name=='gmgn' else ('sol','bsc')
            field='updated'
            stale=[c for c in expected if now-numeric(chains.get(c,{}).get(field))>180]
            if stale:
                add(name,'source_delayed:'+','.join(stale));continue
            if any(state.get('error') for state in chains.values()):
                add(name,'source_retrying');continue
            if name=='signals' and now-numeric(values.get('signal_dashboard',{}).get('generated_at'))>300:
                add(name,'special_snapshot_stalled',True);continue
        if name=='news' and (health.get('error') or health.get('coverage_limited')):
            add(name,'source_delayed');continue
        if name=='export' and not values.get('sync_config',{}).get('enabled',False):
            add(name,'local_only_upload_paused');continue
        add(name,'ok')
    return findings


def inspect(runtime=None,now=None):
    now=time.time() if now is None else now
    runtime=read_runtime() if runtime is None else runtime
    try:
        values=snapshot()
    except (sqlite3.Error,OSError,ValueError):
        return dict(checked_at=now,next_check_at=now+INTERVAL,
                    findings=[dict(service='database',code='read_failed_no_changes',repairable=False)])
    web_ok=False
    try:
        import requests
        web_ok=requests.get('http://127.0.0.1:8501/_stcore/health',timeout=2).ok
    except requests.RequestException:pass
    findings=check(values,runtime,now,
        lambda name,meta:owned(meta.get('pid'),meta.get('created_at'),SERVICES[name]),web_ok)
    return dict(checked_at=now,next_check_at=now+INTERVAL,findings=findings)


def save_report(report):
    # Bounded local log; only controlled codes/PIDs, never provider payloads/keys.
    path=store.USER/'health-audit.jsonl'
    if path.exists() and path.stat().st_size>1024*1024:
        path.replace(path.with_suffix('.previous.jsonl'))
    with path.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n')
    try:store.put('health_audit',report)
    except (sqlite3.Error,OSError,ValueError):pass


def main():
    parser=argparse.ArgumentParser(description='本地采集巡检；--repair 请求启动器安全重启故障进程')
    parser.add_argument('--repair',action='store_true');args=parser.parse_args()
    runtime=read_runtime();report=inspect(runtime)
    if args.repair:
        supervisor=runtime.get('supervisor',{})
        if not runtime.get('stopping') and owned(supervisor.get('pid'),supervisor.get('created_at'),'launch.py'):
            store.put('health_repair_request',dict(requested_at=report['checked_at'],supervisor_pid=supervisor['pid']))
            report['repair_request']='queued_for_confirmation'
        else:report['repair_request']='supervisor_not_running_no_process_touched'
    save_report(report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
