"""No production DB, private key, remote host or GMGN API is touched by these tests."""
import base64
from contextlib import contextmanager
import errno
import io
import json
from pathlib import Path
import socket
import threading
import time

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import data_export as export
import export_worker as worker
import gmgn_monitor
import sftp_sync as sync
import store

NOW = 1800000000.


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'USER', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'monitor.sqlite')
    logger = worker.logging.getLogger('collector_export')
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    yield tmp_path
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def config(**updates):
    return sync.DEFAULTS | dict(enabled=True, host='127.0.0.1', username='monitor',
                                host_key_type='ssh-ed25519',host_key_base64='cHVibGlj') | updates


def seed_market():
    store.put('settings', dict(top_symbols=['BTCUSDT'],spike_up_pct=20,spike_down_pct=20,include_stocks=True))
    store.put('exchange_info', dict(symbols=['BTCUSDT','ALTUSDT','SOXLUSDT','XAUUSDT'],
                                  market_symbols=['BTCUSDT','ALTUSDT','SOXLUSDT','XAUUSDT'],
                                  alt_symbols=['BTCUSDT','ALTUSDT'],stock_symbols=['SOXLUSDT'],commodity_symbols=['XAUUSDT']))
    store.put('live_market',dict(saved_at=NOW,tickers={s:dict(price=110,event_time=int(NOW*1000),
                                quote24h=1e7,change24h=25,private_key='MUST_NOT_EXPORT')
                                for s in ['BTCUSDT','ALTUSDT','SOXLUSDT','XAUUSDT']}))
    with store.connect() as con:
        for s in ['BTCUSDT','ALTUSDT','SOXLUSDT','XAUUSDT']:
            event=int(NOW*1000)
            con.execute('INSERT INTO price_samples VALUES (?,?,?,?)',(s,(event-240*60000)//5000,event-240*60000,100))
        gmgn_monitor.schema(con)
        con.execute("INSERT INTO gmgn_alerts(chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?)",
                    ('sol','a'*32,'今日毕业放量',NOW,NOW,json.dumps(dict(chain='sol',address='a'*32,volume=300001,api_key='MUST_NOT_EXPORT'))))
    store.put('gmgn_config',dict(api_key='MUST_NOT_EXPORT'))
    store.put('gmgn_market',dict(last_success=NOW,hot_updated=NOW,hot=dict(symbol='TOKEN',volume=42,private_key='MUST_NOT_EXPORT')))
    store.put('ai_config',dict(secret='MUST_NOT_EXPORT'))
    store.put('sync_config',config(password='MUST_NOT_EXPORT'))
    store.put('stream_health',dict(connected=True,last_message=NOW,error='MUST_NOT_EXPORT'))
    store.put('market_volume_15d',dict(complete=True,days=[dict(start=123,quote=5,top10_quote=4,top10=[dict(symbol='ALTUSDT',quote=4)])]))


def test_export_uses_current_rules_keeps_asset_scope_and_omits_credentials(isolated):
    seed_market()
    packet=export.build_packet('collector',now=NOW)
    text=export.encode(packet).decode()
    assert 'MUST_NOT_EXPORT' not in text
    market=packet['modules']['binance_market']['data']
    assert market['window_minutes']==240 and market['spike_window_minutes']==15
    assert market['rules']['spike_up_pct']==20
    assert {r['asset_type'] for r in market['rows']}=={'crypto','stock','commodity'}
    assert market['focus']['BTCUSDT']['change']==pytest.approx(10)
    assert set(market['rankings']['up'])=={'ALTUSDT','SOXLUSDT','XAUUSDT'}
    assert packet['modules']['gmgn']['data']['hot_fresh']
    assert packet['modules']['market_volume']['data']['days'][0]['top10_quote']==4
    assert packet['events']['gmgn_alerts']['new'][0]['token']['volume']==300001


def test_future_module_and_failure_are_forward_compatible(isolated,monkeypatch):
    monkeypatch.setattr(export,'SECTIONS',{})
    @export.section('new_monitor',version=2)
    def good(context):return dict(value=3,unknown=float('nan'))
    @export.section('unavailable')
    def bad(context):raise RuntimeError('SECRET')
    packet=export.build_packet('collector',now=NOW)
    assert packet['modules']['new_monitor']['version']==2
    assert packet['modules']['new_monitor']['data']==dict(value=3,unknown=None)
    assert packet['modules']['unavailable']['status']=='unavailable'
    assert 'SECRET' not in export.encode(packet).decode()


def test_event_pagination_cursor_durable_restart_and_recent_ack(isolated,monkeypatch):
    monkeypatch.setattr(export,'EVENT_LIMIT',2)
    with store.connect() as con:
        for n in range(5):
            con.execute('INSERT INTO spike_alerts(time,symbol,direction,change,base_price,price,base_time,event_time,basis) VALUES (?,?,?,?,?,?,?,?,?)',
                        (NOW+n,'ALTUSDT','急涨',20,100,120,1000,2000,'实时'))
    cfg=config()
    first=worker.Worker();first.capture(cfg,NOW)
    second=worker.Worker();second.capture(cfg,NOW+1)
    second.capture(cfg,NOW+2)
    with worker.database() as con:
        packets=[json.loads(r[0]) for r in con.execute('SELECT body FROM packets ORDER BY sequence')]
        assert worker.meta(con,'event_cursor')['spike_alerts']==5
    assert [[r['id'] for r in p['events']['spike_alerts']['new']] for p in packets]==[[1,2],[3,4],[5]]
    assert [p['sequence'] for p in packets]==[1,2,3]
    assert first.collector_id==second.collector_id
    store.acknowledge_spikes([1])
    packet=export.build_packet(first.collector_id,cursor={'spike_alerts':5},now=NOW+3)
    assert packet['events']['spike_alerts']['new']==[]
    assert next(r for r in packet['events']['spike_alerts']['recent'] if r['id']==1)['acknowledged']==1


def test_retry_same_packet_and_bounded_queue_never_advance_lost_cursor(isolated,monkeypatch):
    monkeypatch.setattr(worker,'MAX_PENDING_PACKETS',1)
    calls=[]
    def fail(cfg,name,body):
        calls.append((name,body))
        raise TimeoutError()
    monkeypatch.setattr(sync,'upload',fail)
    service=worker.Worker();cfg=config()
    service.capture(cfg,NOW)
    service.deliver(cfg,NOW)
    assert worker.queue_stats()['pending']==1
    with worker.database() as con:
        row=con.execute('SELECT * FROM packets').fetchone()
        retry=row['retry_at']
        cursor=worker.meta(con,'event_cursor')
    service.capture(cfg,NOW+2)
    with worker.database() as con:
        assert worker.meta(con,'event_cursor')==cursor
        assert con.execute('SELECT COUNT(*) FROM packets').fetchone()[0]==1
    assert store.get('sync_health')['state']=='queue_full'
    service.deliver(cfg,retry-1)
    assert len(calls)==1
    monkeypatch.setattr(sync,'upload',lambda cfg,name,body:calls.append((name,body)) or 'uploaded')
    service.deliver(cfg,retry+1)
    assert calls[0]==calls[1] and worker.queue_stats()['pending']==0
    assert store.get('sync_health')['consecutive_failures']==0
    assert 'attempt=1' in (isolated/'sync.log').read_text(encoding='utf-8')


def test_changed_destination_does_not_receive_old_data_and_disabled_sends_nothing(isolated,monkeypatch):
    service=worker.Worker();calls=[]
    monkeypatch.setattr(sync,'upload',lambda *args:calls.append(args))
    service.capture(config(),NOW)
    service.deliver(config(host='other.example'),NOW+1)
    assert not calls and store.get('sync_health')['state']=='destination_changed'
    service.deliver(config(enabled=False),NOW+2)
    service.capture(config(enabled=False),NOW+3)
    assert not calls and worker.queue_stats()['pending']==1
    assert (isolated/'sync/latest.json').exists()


class FakeSFTP:
    def __init__(self): self.files={};self.renamed=[];self.fail_rename=False
    def stat(self,path):
        if path not in self.files:raise FileNotFoundError(errno.ENOENT,'missing')
        return type('Attr',(),dict(st_size=len(self.files[path])))()
    def open(self,path,mode):return io.BytesIO(self.files[path])
    def putfo(self,source,path,**kwargs):
        assert not any(p.endswith('.json') for p in self.files)
        self.files[path]=source.read()
    def rename(self,source,target):
        self.files[target]=self.files.pop(source);self.renamed.append(target)
        if self.fail_rename:raise TimeoutError('Server renamed file but reply was lost')


def test_atomic_publish_recovers_lost_ack_without_duplicate_or_overwrite():
    server=FakeSFTP();server.fail_rename=True
    with pytest.raises(TimeoutError):sync.publish(server,'/inbox','packet.json',b'{"value":1}')
    assert sync.publish(server,'/inbox','packet.json',b'{"value":1}')=='already_present'
    assert server.renamed==['/inbox/packet.json']
    with pytest.raises(sync.SyncError):sync.publish(server,'/inbox','packet.json',b'{"value":2}')
    assert server.files['/inbox/packet.json']==b'{"value":1}'


def ssh_key():
    key=Ed25519PrivateKey.generate()
    raw=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
    return paramiko.Ed25519Key.from_private_key(io.StringIO(raw.decode()))


@contextmanager
def local_server(folder,host_key,client_key):
    class Auth(paramiko.ServerInterface):
        def check_auth_publickey(self,username,key):
            return paramiko.AUTH_SUCCESSFUL if username=='monitor' and key==client_key else paramiko.AUTH_FAILED
        def get_allowed_auths(self,username):return 'publickey'
        def check_channel_request(self,kind,channel):
            return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    class Files(paramiko.SFTPServerInterface):
        def local(self,path):
            local=(folder/path.lstrip('/')).resolve()
            if not local.is_relative_to(folder.resolve()):raise PermissionError()
            return local
        def stat(self,path):
            try:return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
            except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno)
        def open(self,path,flags,attr):
            try:
                fd=__import__('os').open(self.local(path),flags,0o600)
                mode='r+b' if flags&__import__('os').O_RDWR else 'wb' if flags&__import__('os').O_WRONLY else 'rb'
                stream=__import__('os').fdopen(fd,mode)
                handle=paramiko.SFTPHandle(flags);handle.readfile=stream;handle.writefile=stream
                return handle
            except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno)
        def rename(self,source,target):
            self.local(source).rename(self.local(target));return paramiko.SFTP_OK
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.2)
    port=listener.getsockname()[1];stop=threading.Event();transports=[]
    def run():
        while not stop.is_set():
            try:sock,_=listener.accept()
            except socket.timeout:continue
            except OSError:break
            transport=paramiko.Transport(sock);transports.append(transport)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler('sftp',paramiko.SFTPServer,Files)
            try:transport.start_server(server=Auth())
            except (EOFError,OSError,paramiko.SSHException):transport.close()
    thread=threading.Thread(target=run,daemon=True);thread.start()
    try:yield port
    finally:
        stop.set();listener.close()
        for transport in transports:transport.close()
        thread.join(timeout=3)


def test_real_sftp_key_auth_atomic_json_and_host_mismatch(isolated,monkeypatch):
    host_key=ssh_key();client_key=ssh_key()
    folder=isolated/'server';(folder/'inbox').mkdir(parents=True)
    monkeypatch.setattr(sync,'load_key',lambda:client_key)
    with local_server(folder,host_key,client_key) as port:
        cfg=config(port=port,host_key_base64=host_key.get_base64())
        assert sync.upload(cfg,'sample.json',b'{"ok":true}')=='uploaded'
        assert (folder/'inbox/sample.json').read_bytes()==b'{"ok":true}'
        assert list((folder/'inbox').glob('*.tmp'))==[]
        assert sync.upload(cfg,'sample.json',b'{"ok":true}')=='already_present'
        with pytest.raises(paramiko.BadHostKeyException):
            sync.upload(cfg|dict(host_key_base64=ssh_key().get_base64()),'blocked.json',b'{}')
        assert not (folder/'inbox/blocked.json').exists()


@pytest.mark.parametrize('changes',[
    dict(host='https://example.com'),dict(username='x\nexit'),dict(directory='/inbox/../etc'),
    dict(directory='/inbox\nput'),dict(interval_seconds=1),dict(port=0)])
def test_invalid_config_cannot_inject_paths_or_commands(changes):
    with pytest.raises(ValueError):sync.validate(config(**changes))
