"""Owned child recovery plus hourly, confirmed local health audits."""
import json
import os
import subprocess
import sys
import time
import psutil
import store
import health_check as health


class Supervisor:
    def __init__(self,commands):
        self.commands=commands;self.children={};self.metadata={};self.history={};self.suspects={}
        self.started=time.time();self.next_audit=self.started+300;self.last_request=0
        self.supervisor=dict(pid=os.getpid(),created_at=psutil.Process().create_time())

    def stopping(self):return bool(health.read_runtime().get('stopping'))

    def persist(self):
        # Never erase a stop requested by the launcher/desktop shortcut.
        if self.stopping():return
        target=store.USER/'runtime.json';temp=target.with_suffix('.supervisor.tmp')
        temp.write_text(json.dumps(dict(pids=[p.pid for p in self.children.values()],
            supervisor=self.supervisor,services=self.metadata)),encoding='utf-8')
        temp.replace(target)

    def spawn(self,name):
        env=os.environ.copy();env['PYTHONUTF8']='1'
        with (store.USER/f'{name}.log').open('a',encoding='utf-8') as log:
            child=subprocess.Popen(self.commands[name],cwd=store.ROOT,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,env=env)
        self.children[name]=child
        stamp=time.time()
        try:created=psutil.Process(child.pid).create_time()
        except psutil.NoSuchProcess:created=stamp
        self.metadata[name]=dict(pid=child.pid,created_at=created,started_at=stamp)
        self.persist()

    def start(self):
        for name in self.commands:self.spawn(name)

    def stop_child(self,name):
        child=self.children[name];meta=self.metadata[name]
        if child.poll() is not None:return True
        if not health.owned(child.pid,meta['created_at'],health.SERVICES[name]):return False
        # Terminate only this exact managed worker and descendants carrying
        # an absolute script path inside this installation (e.g. GMGN bridge).
        descendants=[]
        try:
            for proc in psutil.Process(child.pid).children(recursive=True):
                try:
                    if any(os.path.isabs(a) and os.path.commonpath([str(store.ROOT),os.path.abspath(a)])==str(store.ROOT)
                           for a in proc.cmdline()):descendants.append(proc)
                except (psutil.Error,ValueError):pass
        except psutil.Error:pass
        for proc in descendants:
            try:proc.terminate()
            except psutil.Error:pass
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill();child.wait(timeout=5)
        _,remaining=psutil.wait_procs(descendants,timeout=2)
        for proc in remaining:
            try:proc.kill()
            except psutil.Error:pass
        return True

    def restart(self,name,now):
        attempts=[stamp for stamp in self.history.get(name,[]) if now-stamp<3600]
        if len(attempts)>=3 or attempts and now-attempts[-1]<(60,300,900)[min(len(attempts)-1,2)]:
            return 'restart_budget_wait'
        self.history[name]=attempts+[now]
        if self.stopping():return 'stopping_no_restart'
        try:
            if not self.stop_child(name):return 'identity_mismatch_no_restart'
            if self.stopping():return 'stopping_no_restart'
            self.spawn(name)
        except (OSError,psutil.Error,subprocess.TimeoutExpired):return 'restart_failed'
        return 'restarted_pending_health'

    def audit(self,now):
        report=health.inspect(dict(services=self.metadata),now)
        actions=[];pending={}
        for finding in report['findings']:
            name=finding['service']
            if not finding['repairable'] or name not in self.children:continue
            previous=self.suspects.get(name)
            # Two independent observations, >=30s apart, before touching a live worker.
            if previous and previous['code']==finding['code'] and now-previous['since']>=30:
                actions.append(dict(service=name,result=self.restart(name,now)))
            else:pending[name]=dict(code=finding['code'],since=now)
        self.suspects=pending
        self.next_audit=now+(30 if pending else health.INTERVAL)
        if any(action['result']=='restarted_pending_health' for action in actions):
            self.next_audit=min(self.next_audit,now+300)
        report.update(next_check_at=self.next_audit,actions=actions)
        health.save_report(report)

    def tick(self,now=None):
        now=time.time() if now is None else now
        if self.stopping():return False
        # A process exit needs no stale-data inference; recover that service only.
        for name,child in list(self.children.items()):
            if child.poll() is not None:
                values=health.snapshot()
                cfg_key={'gmgn':'gmgn_config','signals':'web3_signal_config','news':'blockbeats_config'}.get(name)
                if cfg_key and values.get(cfg_key,{}).get('enabled') is False:continue
                action=self.restart(name,now)
                if action=='restarted_pending_health':self.next_audit=min(self.next_audit,now+300)
                if action!='restart_budget_wait':health.save_report(dict(checked_at=now,next_check_at=self.next_audit,
                    findings=[dict(service=name,code='process_exited',repairable=True)],actions=[dict(service=name,result=action)]))
        request=store.get('health_repair_request',{})
        requested=health.numeric(request.get('requested_at'))
        if request.get('supervisor_pid')==os.getpid() and self.last_request<requested<=now and now-requested<120:
            self.last_request=requested;self.next_audit=min(self.next_audit,now)
        if now>=self.next_audit:self.audit(now)
        return True

    def close(self):
        for name in self.children:
            try:self.stop_child(name)
            except (psutil.Error,OSError,subprocess.TimeoutExpired):pass
        (store.USER/'runtime.json').unlink(missing_ok=True)
