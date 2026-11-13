"""Install an already tested source-only release on the owned server only."""
from pathlib import Path
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time

BASE=Path('/opt/perp-workbench')
UNIT_DIR=Path('/etc/systemd/system')
UNITS={
'perp-recovery.service':'''[Unit]
Description=Inspect owned monitoring and bounded recovery
After=network-online.target
[Service]
Type=oneshot
ExecStart=/opt/perp-workbench/venv/bin/python -u /opt/perp-workbench/current/deploy/server_watchdog.py
TimeoutStartSec=150
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/perp-workbench/shared/recovery /opt/perp-workbench/shared/user_data /opt/perp-workbench/shared/telegram
Environment=PYTHONDONTWRITEBYTECODE=1
''',
'perp-maintenance.service':'''[Unit]
Description=Full database integrity and verified daily monitor backups
After=perp-workbench.service
[Service]
Type=oneshot
ExecStart=/opt/perp-workbench/venv/bin/python -u /opt/perp-workbench/current/deploy/server_maintenance.py
TimeoutStartSec=180
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/perp-workbench/shared/maintenance /opt/perp-workbench/shared/backups /opt/perp-workbench/shared/user_data /opt/perp-workbench/shared/telegram
Environment=PYTHONDONTWRITEBYTECODE=1
''',
'perp-maintenance.timer':'''[Unit]
Description=Inspect integrity hourly and save one verified backup each day
[Timer]
OnBootSec=120
OnUnitActiveSec=3600
AccuracySec=30
Unit=perp-maintenance.service
[Install]
WantedBy=timers.target
'''}
LOGROTATE='''# Managed for codex-perp-workbench-v1
/opt/perp-workbench/shared/user_data/gmgn.log /opt/perp-workbench/shared/user_data/signals.log /opt/perp-workbench/shared/user_data/workbench.log {
    daily
    maxsize 10M
    rotate 7
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    su perp-monitor perp-monitor
}
'''


def run(args,timeout=45,cwd=None):
    r=subprocess.run(args,capture_output=True,text=True,timeout=timeout,cwd=cwd)
    if r.returncode:raise RuntimeError('operation_failed:'+args[0]+':'+str(r.returncode))
    return r.stdout.strip()


def install(release):
    assert os.geteuid()==0
    assert (BASE/'.deployment-owner').read_text().strip()=='codex-perp-workbench-v1'
    release=release.resolve();assert release.parent==BASE/'releases'
    old=(BASE/'current').resolve();assert old!=release
    manifest=json.loads((release/'server-standard-manifest.json').read_text())
    assert manifest['profile']=='signals-only' and manifest['version']=='server-standard-20261111-v4'
    for n,h in manifest['files'].items():
        p=(release/n).resolve();assert p.is_relative_to(release)
        assert hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()==h
    assert (release/'user_data').resolve()==BASE/'shared/user_data'
    # Fail early if a source was explicitly stopped by the operator.
    assert not (BASE/'shared/recovery/disabled').exists(),'Explicit recovery pause preserved'
    timer_active=run(['systemctl','is-active','perp-recovery.timer'])=='active'
    backup=BASE/'shared'/('standard-upgrade-backup-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path('/etc/perp-workbench/collector.env'),Path('/etc/perp-workbench/telegram.env'))}
    previous={str(UNIT_DIR/n):(UNIT_DIR/n).read_bytes() if (UNIT_DIR/n).exists() else None for n in UNITS}
    logpath=Path('/etc/logrotate.d/perp-workbench')
    if logpath.exists():assert logpath.read_text().startswith('# Managed for codex-perp-workbench-v1'),'Unrelated logrotate file preserved'
    previous[str(logpath)]=logpath.read_bytes() if logpath.exists() else None
    (backup/'previous-units.json').write_text(json.dumps({p:raw.decode() if raw is not None else None for p,raw in previous.items()}))
    switched=False;installed=False
    try:
        if timer_active:run(['systemctl','stop','perp-recovery.timer'])
        run(['systemctl','stop','perp-recovery.service'])
        run(['systemctl','stop','perp-special-telegram.service'])
        run(['systemctl','stop','perp-workbench.service'])
        for name,relative in (('workbench','user_data/workbench.sqlite'),('delivery','telegram/delivery.sqlite')):
            src=sqlite3.connect(BASE/'shared'/relative,timeout=10);dst=sqlite3.connect(backup/(name+'.sqlite'))
            try:src.backup(dst);assert dst.execute('PRAGMA integrity_check(1)').fetchall()==[('ok',)]
            finally:dst.close();src.close()
        for n in ('maintenance','backups'):(BASE/'shared'/n).mkdir(mode=0o700,exist_ok=True)
        link=BASE/'current.standard-new';assert not link.exists();link.symlink_to(release,target_is_directory=True);link.replace(BASE/'current');switched=True
        for n,text in UNITS.items():(UNIT_DIR/n).write_text(text);(UNIT_DIR/n).chmod(0o644)
        if shutil.which('logrotate'):
            logpath.write_text(LOGROTATE);logpath.chmod(0o644)
            run(['logrotate','--debug',str(logpath)])
        run(['systemd-analyze','verify',*[str(UNIT_DIR/n) for n in UNITS]])
        run(['systemctl','daemon-reload'])
        # Planned releases are separate from crash-loop recovery. Clear an
        # exhausted systemd start counter once, without changing its limits.
        run(['systemctl','reset-failed','perp-workbench.service','perp-special-telegram.service'])
        run(['systemctl','start','perp-workbench.service'])
        run(['systemctl','start','perp-special-telegram.service'])
        run(['systemctl','start','perp-maintenance.service'],timeout=185)
        maintenance=json.loads((BASE/'shared/maintenance/status.json').read_text())
        assert maintenance['database_integrity']=='ok'
        assert set(maintenance['backups'])=={'workbench','delivery'}
        assert all(v['status'] in ('saved','already_saved') for v in maintenance['backups'].values())
        run(['systemctl','enable','--now','perp-maintenance.timer'])
        run(['systemctl','start','perp-recovery.service'],timeout=160)
        status=json.loads((BASE/'shared/recovery/status.json').read_text());assert status['release']['ok']
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
        info=dict(version=manifest['version'],old_release=str(old),release=str(release),backup=str(backup),credentials_unchanged=True,
            hourly_integrity=maintenance['database_integrity'],verified_backups=maintenance['backups'],release_integrity=status['release'])
        (backup/'result.json').write_text(json.dumps(info,indent=2));installed=True
        print(json.dumps(info),flush=True)
    except BaseException:
        subprocess.run(['systemctl','stop','perp-maintenance.timer'],capture_output=True,timeout=20)
        for p,raw in previous.items():
            target=Path(p)
            if raw is None:target.unlink(missing_ok=True)
            else:target.write_bytes(raw)
        if switched:
            link=BASE/'current.standard-rollback';link.symlink_to(old,target_is_directory=True);link.replace(BASE/'current')
        subprocess.run(['systemctl','daemon-reload'],capture_output=True,timeout=15)
        subprocess.run(['systemctl','reset-failed','perp-workbench.service','perp-special-telegram.service'],capture_output=True,timeout=15)
        subprocess.run(['systemctl','restart','perp-workbench.service'],capture_output=True,timeout=45)
        subprocess.run(['systemctl','start','perp-special-telegram.service'],capture_output=True,timeout=45)
        raise
    finally:
        if timer_active:subprocess.run(['systemctl','start','perp-recovery.timer'],capture_output=True,timeout=15)


if __name__=='__main__':install(Path(sys.argv[1]))
