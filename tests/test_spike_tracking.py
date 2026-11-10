import pytest
import store
import spike_monitor as s
from monitor_ui import top20_spike_card

NOW=1794214800.
SYMBOL='BTCUSDT'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'tracking.sqlite')
    monkeypatch.setattr(s.time,'time',lambda:NOW)
    store.put('spike_top20_since',NOW-100)
    store.put('exchange_info',dict(market_symbols=[SYMBOL],symbols=[SYMBOL]))
    first=dict(price=100.,time=int((NOW-60)*1000),basis=s.POLICY+' · test')
    assert store.set_spike_episode(SYMBOL,'急涨',True,int(NOW*1000),6.,first,106.)
    with store.connect() as con:
        target=int((NOW-15*60)*1000)
        con.execute('INSERT INTO price_samples VALUES (?,?,?,?)',(SYMBOL,target//5000,target,102.))
    quote(107.)


def quote(price,stamp=NOW):
    store.put('live_market',dict(tickers={SYMBOL:dict(price=price,quote24h=1e9,event_time=int(stamp*1000))}))


def test_first_trigger_is_immutable_and_current_percent_keeps_moving_without_new_events(isolated):
    first=s.snapshot(NOW)['tracked'][0]
    assert first['tracking_change']==pytest.approx(7.)
    assert first['change15m']==pytest.approx((107/102-1)*100)
    assert first['trigger_change']==6 and first['trigger_price']==106
    quote(112.,NOW+1)
    second=s.snapshot(NOW+1)['tracked'][0]
    assert second['tracking_change']==pytest.approx(12.)
    assert second['change15m']==pytest.approx((112/102-1)*100)
    assert second['id']==first['id'] and second['triggered']==first['triggered']
    assert store.spike_alerts()[0][4]==6. and len(store.spike_alerts())==1


def test_rearmed_and_opposite_events_have_only_one_symbol_card_with_first_anchor(isolated):
    reference=dict(price=120.,time=int((NOW-30)*1000),basis=s.POLICY+' · test')
    assert not store.set_spike_episode(SYMBOL,'急跌',True,int((NOW+1)*1000),-10.,reference,108.)
    snapshot=s.snapshot(NOW+1)
    assert len(store.spike_alerts())==1
    assert len(snapshot['records'])==len(snapshot['tracked'])==1
    assert snapshot['tracked'][0]['base_price']==100.  # Never change to a later alarm's base.
    markup=top20_spike_card(NOW+1)
    assert markup.count('data-tracked-symbol="BTCUSDT"')==1
    assert '首报基准' in markup and '近15m' in markup


def test_reversal_and_stale_prices_do_not_freeze_trigger_percentage(isolated):
    quote(95.,NOW+1)
    row=s.snapshot(NOW+1)['tracked'][0]
    assert row['tracking_change']==pytest.approx(-5.) and row['trigger_change']==6.
    stale=s.snapshot(NOW+22)['tracked'][0]
    assert stale['tracking_change'] is None and stale['change15m'] is None and stale['price'] is None
    assert stale['triggered']==NOW
    assert '行情延迟' in top20_spike_card(NOW+22)


def test_already_alerted_symbol_keeps_tracking_after_leaving_top_twenty(isolated):
    values={f'X{i}USDT':dict(price=10.,quote24h=2e9+i,event_time=int(NOW*1000)) for i in range(20)}
    values[SYMBOL]=dict(price=108.,quote24h=1.,event_time=int(NOW*1000))
    store.put('exchange_info',dict(market_symbols=list(values)))
    store.put('live_market',dict(tickers=values))
    snapshot=s.snapshot(NOW)
    assert SYMBOL not in snapshot['universe']
    assert len(snapshot['tracked'])==1 and snapshot['tracked'][0]['in_top20'] is False
    assert snapshot['tracked'][0]['tracking_change']==pytest.approx(8.)


def test_export_includes_live_tracking_and_first_trigger_separately(isolated):
    import data_export
    packet=data_export.binance_15m_anomalies(dict(now=NOW))
    assert packet['display_dedup']=='symbol'
    assert packet['tracked'][0]['tracking_change']==pytest.approx(7.)
    assert packet['records'][0]['change']==6.


def test_exact_four_hour_expiry_then_same_symbol_can_alert_again(isolated):
    assert s.snapshot(NOW+s.LIFETIME-1)['tracked'][0]['expires_at']==NOW+s.LIFETIME
    assert s.snapshot(NOW+s.LIFETIME)['tracked']==[]
    ref=dict(price=200.,time=int((NOW+s.LIFETIME-60)*1000),basis=s.POLICY+' · renewed')
    assert store.set_spike_episode(SYMBOL,'急涨',True,int((NOW+s.LIFETIME)*1000),7.,ref,214.)
    quote(214.,NOW+s.LIFETIME)
    row=s.snapshot(NOW+s.LIFETIME)['tracked'][0]
    assert row['triggered']==NOW+s.LIFETIME and row['base_price']==200.
    assert row['expires_at']==NOW+2*s.LIFETIME
    assert len(store.spike_alerts())==2


def test_old_duplicates_never_extend_the_first_four_hour_window(isolated):
    with store.connect() as con:
        for offset in (60,3*3600):
            con.execute('INSERT INTO spike_alerts(time,symbol,direction,change,base_price,price,base_time,event_time,basis) VALUES (?,?,?,?,?,?,?,?,?)',
                        (NOW+offset,SYMBOL,'急涨',8.,100.,108.,int(NOW*1000),int((NOW+offset)*1000),s.POLICY))
    assert s.snapshot(NOW+3*3600)['tracked'][0]['triggered']==NOW
    assert not s.snapshot(NOW+s.LIFETIME)['tracked']
    assert not s.snapshot(NOW+s.LIFETIME+3600)['tracked']
