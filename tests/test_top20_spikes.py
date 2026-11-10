import pytest
import store
import spike_monitor as s
import live_collector as live

NOW=1794214800.


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'top20.sqlite')
    stamp=[NOW]
    monkeypatch.setattr(live.time,'time',lambda:stamp[0])
    return stamp


def ticks(now=NOW):
    return {f'COIN{i:02d}USDT':dict(price=100.,quote24h=float(100-i)*1000000,event_time=int(now*1000)) for i in range(25)}


def monitor():
    m=live.Monitor();m.ticks=ticks();m.market_valid=set(m.ticks)
    for symbol in m.market_valid:m.spike_prices[symbol].append((int((NOW-60)*1000),100.))
    return m


def test_top20_use_usdt_turnover_not_price_or_watchlist_and_include_btc(isolated):
    values=ticks();values['BTCUSDT']=dict(price=70000.,quote24h=1e12,event_time=int(NOW*1000))
    result=s.top_symbols(values,set(values),NOW)
    assert len(result)==20 and result[0]=='BTCUSDT'
    assert 'COIN18USDT' in result and 'COIN19USDT' not in result
    values['BTCUSDT']['event_time']-=90001
    assert s.top_symbols(values,set(values),NOW)[0]=='COIN00USDT'
    values['COIN00USDT']['quote24h']=float('nan')
    assert 'COIN00USDT' not in s.top_symbols(values,set(values),NOW)


def test_strict_five_percent_only_ranked_twenty_and_both_directions(isolated):
    m=monitor();event=int(NOW*1000)
    m.inspect_spike('COIN00USDT',105.,event)
    m.inspect_spike('COIN01USDT',95.,event)
    m.inspect_spike('COIN20USDT',120.,event)
    assert not store.spike_alerts()
    m.inspect_spike('COIN02USDT',105.01,event)
    m.inspect_spike('COIN03USDT',94.99,event)
    records=store.spike_alerts()
    assert {r[2] for r in records}=={'COIN02USDT','COIN03USDT'}
    assert {r[3] for r in records}=={'急涨','急跌'}
    assert all(s.POLICY in r[9] for r in records)


def test_episode_is_silent_on_repeats_and_survives_restart(isolated):
    m=monitor();event=int(NOW*1000)
    m.inspect_spike('COIN00USDT',106.,event)
    isolated[0]=NOW+1
    m.inspect_spike('COIN00USDT',107.,event+1000)
    isolated[0]=NOW+2
    restarted=monitor();restarted.inspect_spike('COIN00USDT',108.,event+2000)
    assert len(store.spike_alerts())==1
    assert s.snapshot(NOW)['records'][0][2]=='COIN00USDT'
    import sound_ui
    assert sound_ui.snapshot()['events']==[]


def test_ranking_changes_are_applied_and_old_legacy_alerts_not_shown(isolated):
    clock=isolated;m=monitor()
    assert 'COIN24USDT' not in s.top_symbols(m.ticks,m.market_valid,NOW)
    m.inspect_spike('COIN24USDT',120.,int(NOW*1000))
    assert not store.spike_alerts()
    clock[0]+=4;m.ticks['COIN24USDT']['quote24h']=1e12
    m.inspect_spike('COIN24USDT',120.,int(clock[0]*1000))
    assert len(store.spike_alerts())==1
    with store.connect() as con:
        con.execute('INSERT INTO spike_alerts(time,symbol,basis) VALUES (?,?,?)',(NOW,'LEGACY','old threshold'))
    assert len(s.snapshot(clock[0])['records'])==1


@pytest.mark.parametrize('restart',[False,True])
def test_collector_can_renew_after_four_hours_even_when_direction_still_latched(isolated,restart):
    m=monitor();symbol='COIN00USDT';m.inspect_spike(symbol,106.,int(NOW*1000))
    isolated[0]=NOW+60
    m.inspect_spike(symbol,90.,int(isolated[0]*1000))
    assert len(store.spike_alerts())==1
    isolated[0]=NOW+s.LIFETIME
    if restart:m=live.Monitor()
    m.ticks=ticks(isolated[0]);m.market_valid=set(m.ticks)
    m.spike_prices[symbol].append((int((isolated[0]-60)*1000),100.))
    m.inspect_spike(symbol,107.,int(isolated[0]*1000))
    assert len(store.spike_alerts())==2
    assert s.snapshot(isolated[0])['tracked'][0]['triggered']==isolated[0]


def test_special_snapshot_is_checked_once_per_minute_not_on_every_ui_tick(isolated,monkeypatch):
    import web3_signals as w
    store.put('web3_signal_config',dict(enabled=False))
    client=type('NoNetwork',(),{})()
    first=w.Monitor(client).step(NOW)
    isolated[0]=NOW+59
    assert w.Monitor(client).step(NOW+59)['generated_at']==first['generated_at']
    isolated[0]=NOW+60
    assert w.Monitor(client).step(NOW+60)['generated_at']==NOW+60


def test_upgrade_applies_once_preserving_focus_daily_dedup_and_later_settings(isolated):
    import monitor_migrations as migration
    store.put('settings',dict(top_symbols=['BTCUSDT','SOXLUSDT'],spike_enabled=False))
    store.put('gmgn_config',dict(graduate_volume=100000,other_volume=1000000))
    assert migration.apply()
    assert store.settings()['top_symbols']==['BTCUSDT','SOXLUSDT']
    assert store.settings()['spike_top20_only'] is True
    import gmgn_monitor as g
    assert g.configuration()['participant_min']==1
    assert g.configuration()['other_volume']==1000000
    store.put('gmgn_config',g.configuration()|dict(graduate_volume=350000))
    assert not migration.apply()
    assert g.configuration()['graduate_volume']==350000
