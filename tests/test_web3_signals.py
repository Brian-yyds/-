"""Isolated signal/AND/dedup tests. No live wallets, credentials or HTTP."""
from datetime import datetime
import json
from types import SimpleNamespace
import pytest
import store
import gmgn_monitor as g
import web3_signals as w
import signal_ui

NOW=datetime(2026,11,9,12,tzinfo=g.ZONE).timestamp()
ADDRESS='C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'wallet.sqlite')
    monkeypatch.setattr(w.time,'time',lambda:NOW)


def token(chain='sol',address=ADDRESS,**updates):
    return g.normalize(dict(address=address,symbol='TEST',volume=200001,renowned_count=2,
        launchpad='pump',launchpad_platform='Pump.fun',launchpad_status='1',open_timestamp=NOW-60,
        market_cap=123456,holder_count=1234)|updates,chain,NOW)


def raw(chain='sol',address=ADDRESS,**updates):
    return dict(signalId=1,ticker='TEST',chainId=w.CHAINS[chain],contractAddress=address,
        logoUrl='/images/token.png',signalTriggerTime=(NOW-10)*1000,direction='buy',status='active',
        smartMoneyCount=4,currentPrice=None,currentMarketCap=None,alertPrice='1',alertMarketCap='1000',
        exitRate=0)|updates


def save_signal(row=None,chain='sol',**health):
    r=row or w.normalize(raw(),chain,NOW)
    with store.connect() as con:
        w.schema(con)
        con.execute('INSERT OR REPLACE INTO wallet_signals VALUES (?,?,?,?,?,?,?)',
            (chain,r['signal_id'],r['address'],r['triggered'],r['observed'],r['status'],json.dumps(r)))
    store.put('web3_signal_health',dict(chains={chain:dict(updated=NOW,error='')|health}))


def special_count():
    with store.connect() as con:return con.execute('SELECT COUNT(*) FROM special_alerts').fetchone()[0]


def test_current_data_intersection_in_both_arrival_orders_and_daily_dedup(isolated):
    save_signal()
    assert not w.publish(NOW)['special_alerts']  # No synthetic GMGN event from a ticker name.
    g.record_scan('sol',[token()],g.configuration(),NOW)
    first=w.publish(NOW)
    assert len(first['special_alerts'])==1 and first['special_alerts'][0]['token']['volume']==200001
    assert first['special_alerts'][0]['signal']['smart_money_count']==4
    for offset in (1,10,30):w.publish(NOW+offset)
    assert special_count()==1
    save_signal(w.normalize(raw(signalId=2), 'sol',NOW))
    w.publish(NOW+10)
    assert special_count()==1  # A second Web3 signal cannot bypass one-per-day.
    address='0x'+'A'*40
    g.record_scan('bsc',[token('bsc',address)],g.configuration(),NOW)
    r=w.normalize(raw('bsc',address.lower(),signalId=3),'bsc',NOW)
    save_signal(r,'bsc')
    assert len(w.publish(NOW)['special_alerts'])==2  # EVM CA is case insensitive.


@pytest.mark.parametrize('status',['timeout','completed','exitRate','unknown'])
def test_expired_signals_are_display_only_never_intersections(isolated,status):
    g.record_scan('sol',[token()],g.configuration(),NOW)
    save_signal(w.normalize(raw(status=status),'sol',NOW))
    result=w.publish(NOW)
    assert not result['special_alerts'] and result['web3_signals']


@pytest.mark.parametrize('variant',['gmgn_stale','web3_stale','kol','equity','threshold','old_graduate','different_address','disabled'])
def test_missing_stale_and_nonqualifying_data_cannot_create_special(isolated,variant):
    value=token();stamp=NOW
    if variant=='gmgn_stale':stamp=NOW-181
    if variant=='kol':value=token(renowned_count=0,smart_degen_count=0)
    if variant=='equity':value=token(symbol='AAPLx',name='Apple xStock')
    if variant=='threshold':value=token(volume=200000)
    if variant=='old_graduate':value=token(open_timestamp=NOW-86400)
    g.record_scan('sol',[value],g.configuration(),stamp)
    r=w.normalize(raw(),'sol',NOW)
    if variant=='web3_stale':r['observed']=NOW-151
    if variant=='different_address':r['address']='1'*32
    save_signal(r,error='retry' if variant=='web3_error' else '')
    if variant=='disabled':store.put('web3_signal_config',dict(enabled=False))
    assert not w.publish(NOW)['special_alerts']


def test_low_volume_refresh_keeps_accepted_gmgn_alarm_eligible(isolated):
    g.record_scan('sol',[token()],g.configuration(),NOW-20)
    g.record_scan('sol',[token(volume=99999)],g.configuration(),NOW)
    save_signal()
    result=w.publish(NOW)
    assert len(result['special_alerts'])==1
    assert result['special_alerts'][0]['token']['volume']==200001
    assert result['special_alerts'][0]['match_mode']=='alarm_pool_10d'


def test_same_live_web3_event_is_not_reannounced_at_midnight(isolated):
    save_signal();g.record_scan('sol',[token()],g.configuration(),NOW)
    w.publish(NOW)
    tomorrow=NOW+86400
    save_signal(w.normalize(raw(),'sol',tomorrow),updated=tomorrow)
    w.record_gmgn_scan('sol',[token()],tomorrow)
    w.publish(tomorrow)
    assert special_count()==1


def test_normalize_missing_current_values_never_use_trigger_price_and_enforces_identity():
    row=w.normalize(raw(),'sol',NOW)
    assert row['current_price'] is None and row['market_cap'] is None
    assert row['alert_price']==1 and row['logo']=='https://bin.bnbstatic.com/images/token.png'
    assert w.normalize(raw(logoUrl='https://evil.test/a.png'),'sol',NOW)['logo']==''
    for value in (raw(chainId='56'),raw(signalId=True),raw(direction='maybe'),raw(signalTriggerTime=(NOW+6)*1000)):
        assert w.normalize(value,'sol',NOW) is None


class FakeClient:
    def __init__(self):self.calls=[];self.fail=set()
    def page(self,chain,page=1):
        self.calls.append((chain,page))
        if chain in self.fail:raise w.SignalError('test retry',60)
        address=ADDRESS if chain=='sol' else '0x'+'a'*40
        return [w.normalize(raw(chain,address),'sol' if chain=='sol' else 'bsc',NOW)]


def test_persistent_cadence_and_partial_chain_failure_keep_cache_without_false_match(isolated,monkeypatch):
    client=FakeClient();w.Monitor(client).step(NOW)
    assert len(client.calls)==2
    w.Monitor(client).step(NOW+59)
    assert len(client.calls)==2
    client.fail.add('sol')
    monkeypatch.setattr(w.time,'time',lambda:NOW+60)
    result=w.Monitor(client).step(NOW+60)
    assert len(client.calls)==4
    assert len(result['web3_signals'])==2
    assert result['web3_health']['chains']['sol']['error']
    assert not w.signal_is_current(next(r for r in result['web3_signals'] if r['chain']=='sol'),result['web3_health'],NOW+60)
    assert w.signal_is_current(next(r for r in result['web3_signals'] if r['chain']=='bsc'),result['web3_health'],NOW+60)


def test_client_uses_only_documented_public_route_and_no_auth(isolated):
    seen={}
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield json.dumps(dict(code='000000',success=True,data=[raw()])).encode()
    def post(url,**kw):seen.update(url=url,**kw);return Response()
    client=w.Client();client.session=SimpleNamespace(post=post)
    assert client.page('sol')[0]['signal_id']=='1'
    assert seen['url']==w.ENDPOINT and seen['allow_redirects'] is False
    assert seen['json']==dict(chainId='CT_501',page=1,pageSize=100)
    assert not any('key' in k.lower() or 'authorization' in k.lower() for k in seen['headers'])


def test_ui_uses_one_cached_snapshot_and_export_preserves_special(isolated,monkeypatch):
    save_signal();g.record_scan('sol',[token()],g.configuration(),NOW)
    snapshot=w.publish(NOW)
    monkeypatch.setattr(w.Client,'page',lambda *a:(_ for _ in ()).throw(AssertionError('UI cannot fetch')))
    markup=signal_ui.panels(NOW)
    assert all(f'data-signal-kind="{kind}"' in markup for kind in ('gmgn','web3','special'))
    assert f'data-refresh="{NOW}"' in markup
    assert '200.0K' in markup and 'Web3 钱包信号' in markup and '特级警报' in markup
    assert '1h成交额' not in markup
    import data_export
    value=data_export.wallet_signal_dashboard(dict(now=NOW))
    assert len(value['special_alerts'])==1 and value['rules']['graduate_volume']==200000


def test_main_screen_removes_old_sections_and_preserves_focus(isolated):
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('import monitor_ui\nmonitor_ui.render()',default_timeout=30).run()
    assert not app.exception
    text=''.join(str(x.value) for x in app.markdown)
    for label in ('上涨榜','下跌榜','GMGN 警报','Web3 钱包信号','特级警报','重点监控','15分钟异动'):
        assert label in text
    for label in ('OI 异动榜','15分钟急涨急跌','data-market-volume'):
        assert label not in text
