"""Durable local inbox for accepted public observations, not credentials or provider I/O."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import store

KINDS=('gmgn','wallet')


@contextmanager
def lock(kind):
    root=store.USER/'signal_inbox';root.mkdir(exist_ok=True)
    with (root/(kind+'.lock')).open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0);acquired=False
        try:
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);acquired=True
                except OSError:pass
            else:
                import fcntl
                try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
                except BlockingIOError:pass
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream,fcntl.LOCK_UN)


def submit(kind,payload):
    if kind not in KINDS:raise ValueError('Unsupported observation kind')
    root=store.USER/'signal_inbox';root.mkdir(exist_ok=True)
    path=root/f'{time.time_ns():020d}-{kind}-{uuid.uuid4().hex}.json'
    temp=path.with_suffix('.tmp')
    raw=json.dumps(dict(kind=kind,payload=payload),ensure_ascii=False,allow_nan=False)
    with temp.open('x',encoding='utf-8') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)
    # Failed processing deliberately leaves the durable input for replay.
    try:drain(kind)
    except Exception:pass
    return not path.exists()


def apply(job):
    payload=job['payload']
    if job['kind']=='gmgn':
        import gmgn_tracking
        gmgn_tracking.record_scan(payload['chain'],payload['tokens'],payload['cfg'],payload['stamp'])
    elif job['kind']=='wallet':
        import web3_signals
        web3_signals._record_wallet_batch(payload['chain'],payload['rows'],payload['stamp'],payload['health'])
    else:raise ValueError('Unsupported persisted observation kind')


def drain(kind,limit=20):
    if kind not in KINDS:raise ValueError('Unsupported observation kind')
    with lock(kind) as acquired:
        if not acquired:return 0
        files=sorted((store.USER/'signal_inbox').glob('*-'+kind+'-*.json'))[:limit]
        for path in files:
            job=json.loads(path.read_text(encoding='utf-8'))
            if job.get('kind')!=kind:raise ValueError('Observation kind mismatch')
            apply(job)
            # A crash here can replay the commit: alarm and signal IDs make it idempotent.
            path.unlink()
        return len(files)


def drain_all():
    root=store.USER/'signal_inbox';root.mkdir(exist_ok=True)
    report=dict(checked_at=time.time(),processed=0,errors=[])
    for kind in KINDS:
        try:report['processed']+=drain(kind)
        except Exception as exc:
            report['errors'].append(dict(source=kind,code='database_busy' if isinstance(exc,sqlite3.OperationalError) else 'processing_failed'))
    report['pending']=sum(1 for kind in KINDS for _ in root.glob('*-'+kind+'-*.json'))
    if report['pending'] or report['processed'] or report['errors']:
        temp=root/'status.tmp';temp.write_text(json.dumps(report),encoding='utf-8');temp.replace(root/'status.json')
    return report
