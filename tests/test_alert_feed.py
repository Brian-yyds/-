import json
import sqlite3
import pytest
from pathlib import Path
from alert_feed import Feed
import alert_feed_client as client

NOW=1794290400


@pytest.fixture
def feed(tmp_path):
    path=tmp_path/'source.sqlite'
    with sqlite3.connect(path) as con:
        con.executescript('CREATE TABLE special_alerts(id INTEGER PRIMARY KEY,chain,address,triggered,data);CREATE TABLE gmgn_market_caps(chain,address,market_cap,observed);CREATE TABLE kv(key PRIMARY KEY,value);')
        for i in range(1,121):
            address='0x'+f'{i:040x}'
            tok=dict(chain='base',address=address,name=f'Name{i}',symbol=f'T{i}',market_cap=200000,volume=300000)
            con.execute('INSERT INTO special_alerts VALUES (?,?,?,?,?)',(i,'base',address,NOW-i,json.dumps(dict(token=tok))))
        con.execute('INSERT INTO gmgn_market_caps VALUES (?,?,?,?)',('base','0x'+f'{1:040x}',700000,NOW))
    return Feed(path,'server-feed-test')


def test_pagination_beyond_display_fifty_and_idempotent_read_only(feed):
    before=feed.path.read_bytes();status,first=feed.events(0,100,NOW)
    assert status==200 and len(first['items'])==100 and first['has_more'] and first['next_after_id']==100
    status,second=feed.events(100,100,NOW)
    assert len(second['items'])==20 and not second['has_more'] and second['next_after_id']==120
    assert feed.events(0,100,NOW)[1]==first
    assert feed.path.read_bytes()==before
    item=first['items'][0]
    assert item['market_cap_usd']==700000 and item['market_cap_source']=='current'
    assert item['event_id']=='server-feed-test:special:1' and item['ca'] in item['gmgn_url']
    assert all(key in item for key in ('name','ca','market_cap_usd','gmgn_url','text'))


def test_stale_caps_fall_back_to_explicit_trigger_value(feed):
    row=feed.events(0,1,NOW+601)[1]['items'][0]
    assert row['market_cap_usd']==200000 and row['market_cap_source']=='at_alert'
    assert '触发时市值' in row['text']
    assert feed.events(999,50)[0]==409


def test_bad_source_record_is_explicit_and_does_not_block_later_rows(feed):
    with sqlite3.connect(feed.path) as con:con.execute("UPDATE special_alerts SET data='{}' WHERE id=2")
    data=feed.events(0,3,NOW)[1]
    assert data['invalid_ids']==[2] and data['next_after_id']==3
    assert [item['id'] for item in data['items']]==[1,3]


def request(feed):
    def call(cfg,path):
        from urllib.parse import urlsplit,parse_qs
        if path.endswith('/latest'):return feed.latest()
        q=parse_qs(urlsplit(path).query)
        return feed.events(int(q['after_id'][0]),int(q['limit'][0]),NOW)[1]
    return call


def test_receiver_durable_queue_cursor_restart_ack_and_no_loss(feed,tmp_path):
    path=tmp_path/'bot.sqlite';con=client.database(path)
    first=client.poll(con,{},True,request(feed));assert first['has_more']
    client.poll(con,{},request=request(feed));assert len(client.pending(con))==120
    cursor=con.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]
    assert cursor=='120'
    event_id=client.pending(con)[0]['event_id'];client.acknowledge(con,event_id);con.close()
    restarted=client.database(path)
    client.poll(restarted,{},request=request(feed))
    assert len(client.pending(restarted))==119
    assert all(item['event_id']!=event_id for item in client.pending(restarted))
    restarted.close()


def test_default_bootstrap_does_not_send_old_history(feed,tmp_path):
    con=client.database(tmp_path/'new.sqlite')
    client.poll(con,{},request=request(feed))
    assert not client.pending(con)
    assert con.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]=='120'
    con.close()


def test_failed_fetch_and_feed_reset_never_advance_cursor(feed,tmp_path):
    con=client.database(tmp_path/'new.sqlite');client.poll(con,{},request=request(feed))
    def fail(*a):raise OSError('network')
    with pytest.raises(OSError):client.poll(con,{},request=fail)
    def replaced(*a):return dict(feed_id='different')
    with pytest.raises(ValueError,match='identity_changed'):client.poll(con,{},request=replaced)
    assert con.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]=='120'
    con.close()


def test_http_auth_parameter_validation_and_read_only_methods(feed):
    from alert_feed import Server
    import threading
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    server=Server(('127.0.0.1',0),feed,'test-token',None)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root='http://127.0.0.1:'+str(server.server_port)
    def call(path,token='test-token',method='GET'):
        req=Request(root+path,headers={'Authorization':'Bearer '+token},method=method)
        try:
            with urlopen(req,timeout=3) as result:return result.status,json.load(result)
        except HTTPError as error:return error.code,json.load(error)
    try:
        assert call('/v1/alerts',token='wrong')[0]==401
        assert call('/v1/alerts?limit=101')[0]==400
        assert call('/v1/alerts?limit=2&limit=3')[0]==400
        assert call('/v1/alerts?after_id=-1')[0]==400
        assert call('/v1/alerts',method='POST')[0]==405
        assert call('/v1/alerts?after_id=999')[0]==409
        assert call('/v1/alerts?limit=1')[1]['items'][0]['id']==1
        assert call('/v1/alerts/latest')[1]['latest_id']==120
        assert call('/v1/health')[1]['status']=='ok'
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
