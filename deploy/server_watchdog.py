"""Independent, bounded recovery for this deployment only. No provider API calls."""
import json
from contextlib import closing
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import psutil
import requests

BASE=Path('/opt/perp-workbench')
STATE=BASE/'shared/recovery'
UNITS=('perp-workbench.service','perp-special-telegram.service')
EXPECTED={'gmgn':('sol','bsc','base','robinhood'),'signals':('sol','bsc')}


def number(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else 0


def evaluate(values,runtime,services,children,telegram,web_ok,now):
    """No alerts is healthy. Explicit pauses and provider backoffs are preserved."""
    faults={u:[] for u in UNITS};notes=[]
    main,delivery=UNITS
    for unit in UNITS:
        info=services[unit]
        if info['state'] in ('activating','deactivating'):notes.append(unit+':transition');continue
        if info['state']!='active':faults[unit].append('service_down')
    if services[main]['state']=='active' and now-services[main]['started']>=180:
        if not web_ok:faults[main].append('page_unresponsive')
        if runtime.get('stopping'):
            notes.append('monitor_explicitly_stopping')
        else:
            for name,key,cfg in (('gmgn','gmgn_market','gmgn_config'),('signals','web3_signal_health','web3_signal_config')):
                if values.get(cfg,{}).get('enabled') is False:
                    notes.append(name+':paused');continue
                health=values.get(key,{})
                if not children.get(name):faults[main].append(name+':process_missing');continue
                backoff=name=='gmgn' and number(values.get('gmgn_rate_limit_until'))>now
                if backoff:
                    # Provider Retry-After may legitimately be long. Preserve it;
                    # missing processes were checked above, before this branch.
                    notes.append(name+':provider_backoff');continue
                if now-number(health.get('heartbeat'))>180:faults[main].append(name+':heartbeat_stalled')
                for chain in EXPECTED[name]:
                    detail=health.get('chains',{}).get(chain,{})
                    retry=max(number(detail.get('retry_at')),number(detail.get('next_poll_at')))
                    last=max(number(detail.get('last_attempt')),number(detail.get('updated')))
                    # A retained error string does not prove that retries still run.
                    if retry<=now and now-last>180:
                        faults[main].append(name+':scan_stalled:'+chain)
                        continue
                    if detail.get('capped') or detail.get('coverage_status') in ('pagination_no_progress','continuing'):
                        notes.append(name+':source_coverage_limited:'+chain)
                    if detail.get('error') or retry>now:
                        if detail.get('error'):notes.append(name+':provider_retrying:'+chain)
                        continue
            if values.get('web3_signal_config',{}).get('enabled') is not False and now-number(values.get('signal_dashboard',{}).get('generated_at'))>180:
                faults[main].append('special_snapshot_stalled')
            if not children.get('workbench'):faults[main].append('page_process_missing')
            inbox=values.get('_inbox',{})
            if inbox.get('pending') and now-number(inbox.get('oldest'))>180:
                faults[main].append('accepted_observations_stalled')
    if services[delivery]['state']=='active' and now-services[delivery]['started']>=90:
        if now-number(telegram.get('heartbeat'))>90:faults[delivery].append('delivery_heartbeat_stalled')
        if telegram.get('failed',0) or telegram.get('uncertain',0):notes.append('delivery_records_need_review')
        if telegram.get('pending_due'):notes.append('delivery_pending_or_retrying')
    return faults,sorted(set(notes))


def decide(faults,previous,now):
    suspects={};actions=[];notes=[]
    history={u:[t for t in previous.get('history',{}).get(u,[]) if 0<=now-t<3600] for u in UNITS}
    for unit,codes in faults.items():
        if not codes:continue
        old=previous.get('suspects',{}).get(unit,{})
        unchanged=old.get('codes')==sorted(codes)
        since=old.get('since',now) if unchanged else now
        suspects[unit]=dict(codes=sorted(codes),since=since)
        if 'service_down' not in codes and now-since<25:continue
        attempts=history[unit]
        if len(attempts)>=3 or attempts and now-attempts[-1]<300:
            notes.append(unit+':recovery_cooldown');continue
        actions.append(unit);history[unit].append(now)
        suspects.pop(unit,None)
    return dict(suspects=suspects,history=history),actions,notes


def read_source():
    keys=('gmgn_config','gmgn_market','gmgn_rate_limit_until','web3_signal_config','web3_signal_health','signal_dashboard')
    path=BASE/'shared/user_data/workbench.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as con:
        return {key:json.loads(raw) for key,raw in con.execute('SELECT key,value FROM kv WHERE key IN ('+','.join('?' for _ in keys)+')',keys)}


def read_telegram(now):
    path=BASE/'shared/telegram/delivery.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as con:
        meta=dict(con.execute("SELECT key,value FROM meta WHERE key IN ('heartbeat','api_blocked_until')"))
        counts=dict(con.execute('SELECT status,COUNT(*) FROM delivery GROUP BY status'))
        pending=con.execute("SELECT COUNT(*),MAX(next_try) FROM delivery WHERE status='pending' AND next_try<=?",(now,)).fetchone()
    return dict(heartbeat=float(meta.get('heartbeat',0)),blocked_until=float(meta.get('api_blocked_until',0)),
        pending_due=pending[0],latest_due=pending[1] or 0,failed=counts.get('failed',0),uncertain=counts.get('uncertain',0))


def release_check():
    """Compare current files to the approved deployment, never fetch or overwrite code."""
    root=(BASE/'current').resolve();path=root/'server-standard-manifest.json'
    try:
        manifest=json.loads(path.read_text());mismatch=[]
        for name,expected in manifest['files'].items():
            target=(root/name).resolve()
            if not target.is_relative_to(root) or not target.is_file():mismatch.append(name);continue
            if hashlib.sha256(target.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=expected:mismatch.append(name)
        return dict(version=manifest['version'],ok=not mismatch,mismatched=mismatch)
    except (OSError,ValueError,KeyError,TypeError):return dict(ok=False,error='release_manifest_unreadable')


def inbox_status():
    paths=[p for kind in ('gmgn','wallet') for p in (BASE/'shared/user_data/signal_inbox').glob('*-'+kind+'-*.json')]
    return dict(pending=len(paths),oldest=min((p.stat().st_mtime for p in paths),default=0))


def unit_info(unit):
    result=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','MainPID','-p','FragmentPath'],capture_output=True,text=True,timeout=8,check=True)
    fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if fields.get('FragmentPath')!='/etc/systemd/system/'+unit:raise ValueError('unexpected_unit')
    pid=int(fields.get('MainPID','0'));started=0
    if pid:
        try:started=psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:pass
    return dict(state=fields['ActiveState'],started=started,pid=pid)


def owned_child(meta,name):
    expected={'gmgn':'gmgn_monitor.py','signals':'web3_signals.py','workbench':'app.py'}[name]
    try:
        p=psutil.Process(meta['pid'])
        return abs(p.create_time()-meta['created_at'])<.01 and any(Path(arg).is_absolute() and Path(arg).resolve()==(BASE/'current'/expected).resolve() for arg in p.cmdline())
    except (psutil.Error,OSError,KeyError,ValueError):return False


def save(report):
    temp=STATE/'status.tmp';temp.write_text(json.dumps(report,ensure_ascii=True));temp.replace(STATE/'status.json')
    log=STATE/'audit.jsonl'
    if log.exists() and log.stat().st_size>1024*1024:log.replace(STATE/'audit.previous.jsonl')
    with log.open('a') as stream:stream.write(json.dumps(report,ensure_ascii=True)+'\n')


def run():
    import fcntl
    os.umask(0o077)
    assert (BASE/'.deployment-owner').read_text().strip()=='codex-perp-workbench-v1'
    STATE.mkdir(mode=0o700,exist_ok=True)
    with (STATE/'watchdog.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        now=time.time()
        if (STATE/'disabled').exists():
            print('{"status":"recovery_explicitly_disabled"}',flush=True);return
        status=STATE/'status.json'
        previous=json.loads(status.read_text()) if status.exists() else {}
        try:
            services={u:unit_info(u) for u in UNITS}
            values=read_source();telegram=read_telegram(now);values['_inbox']=inbox_status()
            runtime_path=BASE/'shared/user_data/runtime.json'
            try:runtime=json.loads(runtime_path.read_text())
            except FileNotFoundError:runtime={}
            children={name:owned_child(runtime.get('services',{}).get(name,{}),name) for name in ('gmgn','signals','workbench')}
            try:web_ok=requests.get('http://127.0.0.1:8501/_stcore/health',timeout=3).ok
            except requests.RequestException:web_ok=False
            faults,notes=evaluate(values,runtime,services,children,telegram,web_ok,now)
            release=release_check()
            if not release['ok']:notes.append('release_integrity_requires_review')
            hourly=BASE/'shared/maintenance/status.json'
            maintenance=json.loads(hourly.read_text()) if hourly.exists() else {}
            if now-number(maintenance.get('checked_at'))>4500:notes.append('hourly_inspection_stale')
            if any(v.get('status')=='backup_failed' for v in maintenance.get('backups',{}).values()):notes.append('daily_backup_failed')
            if maintenance.get('notes'):notes.extend('maintenance:'+n for n in maintenance['notes'])
            # Disk corruption cannot be cured by cycling every service. Keep
            # collecting evidence and raise a visible non-repairable finding.
            unsafe=maintenance.get('database_integrity') not in (None,'ok') or maintenance.get('disk_critical')
            if unsafe:
                notes.append('storage_requires_review_no_restart')
                faults={u:[] for u in UNITS}
            recovery,actions,limits=decide(faults,previous,now)
            results=[]
            # Reserve restart budget durably before attempting any mutation.
            report=recovery|dict(checked_at=now,faults=faults,notes=notes+limits,actions=results,
                release=release,inbox=values['_inbox'],maintenance_checked_at=maintenance.get('checked_at'),telegram=telegram)
            save(report)
            for unit in actions:
                # Fixed allowlist only. Never touch unrelated services or provider config.
                assert unit in UNITS
                reset=subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=8)
                try:
                    action=subprocess.run(['systemctl','restart',unit],capture_output=True,timeout=70)
                    results.append(dict(unit=unit,result='restart_requested' if action.returncode==0 else 'restart_failed'))
                except subprocess.TimeoutExpired:results.append(dict(unit=unit,result='restart_timeout'))
            report.update(finished_at=time.time());save(report)
            print(json.dumps(dict(checked_at=now,actions=results,faults=faults,notes=notes+limits)),flush=True)
        except Exception:
            # Unreadable/corrupt state never justifies deleting data or cycling services.
            save(previous|dict(checked_at=now,error='inspection_failed_no_restart'))
            print('{"error":"inspection_failed_no_restart"}',flush=True)


if __name__=='__main__':run()
