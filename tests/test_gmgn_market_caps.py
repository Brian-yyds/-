"""Five-minute cap overlays must never re-arm/replay alarms or call APIs from UI."""
from datetime import datetime
import json
import pytest
import store
import gmgn_monitor as g
import gmgn_market_caps as caps
import web3_signals as w
import sound_ui
import signal_ui

NOW=datetime(2026,11,9,12,tzinfo=g.ZONE).timestamp()
ADDRESS='C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'caps.sqlite')
    clock=[NOW];monkeypatch.setattr(caps.time,'time',lambda:clock[0]);return clock


def token(**updates):
    return g.normalize(dict(address=ADDRESS,symbol='TEST',name='Test',volume=200001,
        launchpad='pump',launchpad_platform='Pump.fun',launchpad_status='1',open_timestamp=NOW-60,
        renowned_count=1,smart_degen_count=0,market_cap=123456,holder_count=100)|updates,'sol',NOW)


def alarm_rows():
    with store.connect() as con:
        return con.execute('SELECT id,rule,triggered,last_seen,acknowledged,data FROM gmgn_alerts ORDER BY id').fetchall()


def test_piggyback_below_threshold_updates_only_after_five_minutes_and_is_silent(isolated):
    original=token();g.record_scan('sol',[original],g.DEFAULTS,NOW)
    before=alarm_rows();sound=sound_ui.snapshot()
    for offset in (60,299):
        isolated[0]=NOW+offset
        g.record_scan('sol',[original|dict(volume=0,renowned_count=0,market_cap=987654)],g.DEFAULTS,NOW+offset)
        assert w.publish(NOW+offset)['gmgn_alerts'][0]['current_market_cap']==123456
    isolated[0]=NOW+300
    g.record_scan('sol',[original|dict(volume=0,market_cap=987654)],g.DEFAULTS,NOW+300)
    current=w.publish(NOW+300)['gmgn_alerts'][0]
    assert current['current_market_cap']==987654 and current['market_cap_observed']==NOW+300
    assert current['triggered']==NOW and current['token']['market_cap']==123456
    assert alarm_rows()==before and sound_ui.snapshot()==sound
    markup=signal_ui.gmgn_card(w.dashboard(),NOW+300)
    assert '$987.7K' in markup and '市值更新' in markup and '每5分钟同步' in markup
    import data_export
    exported=data_export.wallet_signal_dashboard({'now':NOW+300})
    assert exported['gmgn_alerts'][0]['current_market_cap']==987654
    assert exported['rules']['market_cap_interval_seconds']==300


@pytest.mark.parametrize('invalid',[None,float('nan'),float('inf'),-1,True])
def test_bad_or_out_of_order_values_preserve_last_cap_and_timestamp(isolated,invalid):
    original=token();g.record_scan('sol',[original],g.DEFAULTS,NOW)
    with store.connect() as con:
        assert not caps.observe(con,original|dict(market_cap=invalid),NOW+300)
        assert not caps.observe(con,original|dict(market_cap=999),NOW-300)
        assert caps.current(con,original,NOW+300)['market_cap_observed']==NOW
        assert caps.current(con,original,NOW+601)['market_cap_stale'] is True


class Client:
    def __init__(self):self.calls=[]
    def query(self,args):
        self.calls.append(args)
        return [dict(chain=p['chain'],address=p['address'],ok=True,observed=caps.time.time(),
                     data=dict(address=p['address'],price=dict(price='2',volume_5m=1),
                               circulating_supply='250000',wallet_tags_stat={}))
                for p in json.loads(args[args.index('--params')+1])]


def test_expired_watch_visible_alarm_still_refreshes_and_never_creates_event(isolated):
    original=token();g.record_scan('sol',[original],g.DEFAULTS,NOW)
    isolated[0]=NOW+11*86400
    before=alarm_rows();client=Client()
    assert not g.tracking.watch_rows(isolated[0])
    assert caps.refresh(client,g.DEFAULTS)==dict(attempted=1,succeeded=1)
    assert len(client.calls)==1 and alarm_rows()==before
    snapshot=w.publish(isolated[0]);row=snapshot['gmgn_alerts'][0]
    assert row['current_market_cap']==500000 and row['triggered']==NOW
    assert caps.refresh(client,g.DEFAULTS)==dict(attempted=0,succeeded=0)
    assert len(client.calls)==1
    isolated[0]+=299;caps.refresh(client,g.DEFAULTS);assert len(client.calls)==1
    isolated[0]+=1;caps.refresh(client,g.DEFAULTS);assert len(client.calls)==2


def test_reuses_existing_detail_response_without_fallback_calls(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    isolated[0]=NOW+300
    g.tracking.refresh_info(Client(),g.DEFAULTS)
    client=Client()
    assert caps.refresh(client,g.DEFAULTS)==dict(attempted=0,succeeded=0)
    assert not client.calls
    assert w.publish(isolated[0])['gmgn_alerts'][0]['current_market_cap']==500000


def test_failure_interval_survives_restart_and_respects_provider_pause(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW);isolated[0]=NOW+300
    class Failed(Client):
        def query(self,args):self.calls.append(args);raise g.QueryError('test',60)
    client=Failed()
    with pytest.raises(g.QueryError):caps.refresh(client,g.DEFAULTS)
    assert store.get('gmgn_cap_health')['failed']==1
    isolated[0]+=60
    assert caps.refresh(Client(),g.DEFAULTS)==dict(attempted=0,succeeded=0)
    isolated[0]=NOW+600
    store.put('gmgn_rate_limit_until',NOW+900)
    assert caps.refresh(Client(),g.DEFAULTS)==dict(attempted=0,succeeded=0)
    store.put('gmgn_rate_limit_until',0)
    assert caps.refresh(Client(),g.DEFAULTS|dict(enabled=False))==dict(attempted=0,succeeded=0)
    row=w.publish(isolated[0]+1)['gmgn_alerts'][0]
    assert row['current_market_cap']==123456 and row['market_cap_stale']


def test_identity_scoped_and_ui_publish_never_fetches(isolated,monkeypatch):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    with store.connect() as con:
        assert caps.observe(con,token(address='1'*32,market_cap=777),NOW)
        assert caps.current(con,token(),NOW)['current_market_cap']==123456
    monkeypatch.setattr(g.Client,'query',lambda *a:(_ for _ in ()).throw(AssertionError('UI must not fetch')))
    assert 'GMGN 警报' in signal_ui.panels(NOW)
    assert w.publish(NOW)['gmgn_alerts'][0]['current_market_cap']==123456
