"""Independent collector packaging / durable FIFO SFTP delivery worker."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import time
import uuid

import data_export
import sftp_sync
import store

MAX_PACKET_BYTES = 20 * 1024 * 1024
MAX_PENDING_PACKETS = 720
MAX_PENDING_BYTES = 256 * 1024 * 1024


def root():
    path = store.USER / 'sync'
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def database():
    con = sqlite3.connect(root() / 'outbox.sqlite', timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        con.execute('''CREATE TABLE IF NOT EXISTS packets (
            sequence INTEGER PRIMARY KEY,packet_id TEXT UNIQUE,created REAL,destination TEXT,
            filename TEXT,body BLOB,sha256 TEXT,sent REAL,attempts INTEGER DEFAULT 0,
            retry_at REAL DEFAULT 0,last_error TEXT DEFAULT '')''')
        with con:
            yield con
    finally:
        con.close()


def meta(con, key, default=None):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(con, key, value):
    con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


def identity():
    with database() as con:
        ident = meta(con, 'collector_id')
        if not ident:
            ident = str(uuid.uuid4())
            set_meta(con, 'collector_id', ident)
    return ident


def queue_stats():
    with database() as con:
        row = con.execute('SELECT COUNT(*),COALESCE(SUM(length(body)),0),MIN(created) FROM packets WHERE sent IS NULL').fetchone()
    return dict(pending=row[0], pending_bytes=row[1], oldest_pending_at=row[2])


def status(**values):
    store.put('sync_health', store.get('sync_health', {}) | values | queue_stats() | {'heartbeat': time.time()})


def atomic_write(path, body):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Worker:
    def __init__(self):
        self.collector_id = identity()
        self.logger = logging.getLogger('collector_export')
        if not self.logger.handlers:
            handler = RotatingFileHandler(store.USER / 'sync.log', maxBytes=1024*1024,
                                          backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def capture(self, cfg, now):
        with database() as con:
            sequence = int(meta(con, 'sequence', 0)) + 1
            cursor = meta(con, 'event_cursor', {})
        packet = data_export.build_packet(self.collector_id, sequence, cursor, now=now)
        body = data_export.encode(packet)
        if len(body) > MAX_PACKET_BYTES:
            raise sftp_sync.SyncError('数据包超过20MB，已暂停入队；请检查新增导出模块')
        atomic_write(root() / 'latest.json', body)
        queued = False
        if cfg.get('enabled'):
            cfg = sftp_sync.validate(cfg)
            stats = queue_stats()
            if stats['pending'] >= MAX_PENDING_PACKETS or stats['pending_bytes'] + len(body) > MAX_PENDING_BYTES:
                status(state='queue_full', error='待传队列已达上限，保留已有包；新的警报游标未推进，恢复后继续读取本机保留记录')
                return
            stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            filename = f"monitor-{self.collector_id}-{sequence:09d}-{stamp}-{packet['packet_id']}.json"
            with database() as con:
                con.execute('INSERT INTO packets(sequence,packet_id,created,destination,filename,body,sha256) VALUES (?,?,?,?,?,?,?)',
                            (sequence, packet['packet_id'], now, sftp_sync.destination(cfg), filename,
                             body, hashlib.sha256(body).hexdigest()))
                set_meta(con, 'event_cursor', packet['event_cursor']['after'])
                set_meta(con, 'sequence', sequence)
            queued = True
        status(last_capture_at=now, last_packet_id=packet['packet_id'], last_packet_bytes=len(body),
               module_status={k: v['status'] for k, v in packet['modules'].items()},
               state='queued' if queued else 'local_only')

    def deliver(self, cfg, now):
        if not cfg.get('enabled'):
            return
        cfg = sftp_sync.validate(cfg)
        with database() as con:
            row = con.execute('SELECT * FROM packets WHERE sent IS NULL ORDER BY sequence LIMIT 1').fetchone()
        if not row:
            return
        if row['destination'] != sftp_sync.destination(cfg):
            status(state='destination_changed', error='待传包属于先前的服务器或指纹；已保留，需恢复原目标后继续发送')
            return
        if now < row['retry_at']:
            return
        try:
            if hashlib.sha256(row['body']).hexdigest() != row['sha256']:
                raise sftp_sync.SyncError('本机待传数据包校验失败，已停止发送该包')
            result = sftp_sync.upload(cfg, row['filename'], row['body'])
        except Exception as error:
            message = sftp_sync.safe_error(error)
            attempts = row['attempts'] + 1
            retry_at = time.time() + min(600, 10 * 2 ** min(attempts-1, 6))
            with database() as con:
                con.execute('UPDATE packets SET attempts=?,retry_at=?,last_error=? WHERE sequence=?',
                            (attempts, retry_at, message, row['sequence']))
            old = store.get('sync_health', {})
            status(state='retrying', error=message, retry_at=retry_at,
                   consecutive_failures=old.get('consecutive_failures', 0)+1)
            self.logger.warning('packet=%s attempt=%s error=%s', row['packet_id'], attempts, message)
            return
        sent = time.time()
        with database() as con:
            con.execute('UPDATE packets SET sent=?,last_error=? WHERE sequence=?', (sent, '', row['sequence']))
            # Only successful packets can be pruned. Never silently discard pending data.
            con.execute('DELETE FROM packets WHERE sent IS NOT NULL AND sequence NOT IN '
                        '(SELECT sequence FROM packets WHERE sent IS NOT NULL ORDER BY sequence DESC LIMIT 3)')
        status(state='sent', error='', last_success_at=sent, last_sent_filename=row['filename'],
               last_sent_sequence=row['sequence'], retry_at=0, consecutive_failures=0)
        self.logger.info('packet=%s sequence=%s result=%s bytes=%s', row['packet_id'], row['sequence'], result, len(row['body']))

    def cycle(self, now=None):
        now = time.time() if now is None else now
        cfg = sftp_sync.configuration()
        interval = max(30, min(86400, int(cfg['interval_seconds'])))
        request = store.get('sync_capture_request', '')
        with database() as con:
            last_capture = meta(con, 'last_capture', 0)
            last_request = meta(con, 'last_request', '')
        if now - last_capture >= interval or request != last_request:
            self.capture(cfg, now)
            with database() as con:
                set_meta(con, 'last_capture', now)
                set_meta(con, 'last_request', request)
            last_capture = now
        self.deliver(cfg, now)
        status(enabled=bool(cfg['enabled']), next_capture_at=last_capture+interval,
               state='local_only' if not cfg['enabled'] else store.get('sync_health', {}).get('state', 'waiting'))


@contextmanager
def exclusive_worker():
    path = root() / 'worker.lock'
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description='Public monitor collector / SFTP exporter')
    parser.add_argument('--once', action='store_true', help='Run one packaging/delivery cycle')
    args = parser.parse_args()
    with exclusive_worker():
        worker = Worker()
        while True:
            try:
                worker.cycle()
            except Exception as error:
                message = sftp_sync.safe_error(error)
                worker.logger.error('cycle_error=%s', message)
                try:
                    status(state='error', error=message)
                except Exception:
                    worker.logger.error('status_write_failed')
            if args.once:
                break
            time.sleep(2)


if __name__ == '__main__':
    main()
