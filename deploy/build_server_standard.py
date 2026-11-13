"""Build a source-only server overlay; credentials and runtime data never enter it."""
from pathlib import Path
import hashlib,json,re,sys,zipfile

CORE=('store.py','health_check.py','service_supervisor.py','gmgn_monitor.py','gmgn_tracking.py',
 'gmgn_market_caps.py','gmgn_credentials.py','gmgn_equities.py','gmgn_bridge.mjs','gmgn_equity_registry.json',
 'web3_signals.py','signal_ingest.py','signal_ui.py','signal_scroll.html','sound_ui.py','alert_sound.html')
TESTS=('test_server_watchdog.py','test_server_maintenance.py','test_server_profile.py','test_web3_signals.py',
 'test_special_alarm_pool.py','test_gmgn_extra_rule.py','test_gmgn_monitor.py','test_gmgn_market_caps.py',
 'test_gmgn_reliability.py','test_health_check.py','test_sound_ui.py','test_database_resilience.py','test_telegram_special.py')


def build(destination):
    root=Path(__file__).resolve().parents[1]
    files={n:(root/n).read_bytes() for n in CORE}
    for n in ('server_app.py','server_launch.py','server_watchdog.py','server_maintenance.py','install_server_standard.py','telegram_special.py','perp-special-telegram.service.example','telegram.example'):
        files['deploy/'+n]=(root/'deploy'/n).read_bytes()
    files['app.py']=files['deploy/server_app.py'];files['launch.py']=files['deploy/server_launch.py']
    for n in TESTS:files['tests/'+n]=(root/'tests'/n).read_bytes()
    manifest=dict(version='server-standard-20261111-v4',profile='signals-only',
       match_engine='2026-11-09-event-pool-v3',files={n:hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
          for n,raw in files.items() if not n.startswith('tests/')})
    files['server-standard-manifest.json']=json.dumps(manifest,indent=2).encode()
    for n,raw in files.items():
        assert not re.search(rb'gmgn_[a-fA-F0-9]{32}|bbp_[A-Za-z0-9]{24,128}|\b\d{8,12}:[A-Za-z0-9_-]{30,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY',raw),'Possible credential in '+n
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
        for n,raw in files.items():z.writestr(n,raw)
    print(json.dumps(dict(archive=str(destination),sha256=hashlib.sha256(Path(destination).read_bytes()).hexdigest(),source_files=len(files),version=manifest['version'])))


if __name__=='__main__':build(sys.argv[1])
