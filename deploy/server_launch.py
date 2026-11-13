"""Server-only supervisor. No Binance-price, news or export child is started."""
import os
import signal
import sys
import time
import requests
import store
import health_check
from service_supervisor import Supervisor

# Only managed services are inspected in this server variant.
health_check.SERVICES={'gmgn':'gmgn_monitor.py','signals':'web3_signals.py','workbench':'app.py'}


def start():
    os.chdir(store.ROOT);store.USER.mkdir(exist_ok=True)
    try:
        if requests.get('http://127.0.0.1:8501/_stcore/health',timeout=1).ok:
            raise RuntimeError('Monitor port already in use; duplicate launch refused')
    except requests.RequestException:pass
    runtime=health_check.read_runtime()
    previous=runtime.get('supervisor',{})
    if health_check.owned(previous.get('pid'),previous.get('created_at'),'launch.py'):
        raise RuntimeError('Previous owned supervisor is still running')
    (store.USER/'runtime.json').unlink(missing_ok=True)
    commands={
        'gmgn':[sys.executable,str(store.ROOT/'gmgn_monitor.py')],
        'signals':[sys.executable,str(store.ROOT/'web3_signals.py')],
        'workbench':[sys.executable,'-m','streamlit','run',str(store.ROOT/'app.py'),
                     '--server.address','127.0.0.1','--server.port','8501','--server.headless','true',
                     '--browser.gatherUsageStats','false'],
    }
    supervisor=Supervisor(commands)
    stopping=False
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        supervisor.start()
        print('Server-only GMGN / Web3 / special monitor started',flush=True)
        while not stopping:
            try:
                if not supervisor.tick():break
            except Exception:print('Local audit unavailable; preserving healthy workers',flush=True)
            time.sleep(2)
    finally:supervisor.close()


if __name__=='__main__':start()
