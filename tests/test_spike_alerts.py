import time
import pytest
import store
import monitoring as m
from live_collector import Monitor


def test_exact_ten_percent_both_directions():
    now=10_000_000
    up=m.spike_metrics([(now-60000,100)],[],110,now)
    down=m.spike_metrics([(now-60000,100)],[],90,now)
    assert up['up_pct']==pytest.approx(10)
    assert down['down_pct']==pytest.approx(-10)


def test_only_references_inside_fifteen_minutes_are_used():
    now=10_000_000
    result=m.spike_metrics([(now-900001,50),(now-900000,100)],[],110,now)
    assert result['up_pct']==pytest.approx(10)
    assert m.spike_metrics([(now-900001,50)],[],110,now) is None


def test_bootstrap_uses_only_whole_completed_minutes_in_window():
    now=10_020_000
    # t,open,high,low,close,quote,closed,seen
    bars=[[now-900001,100,200,1,100,0,1,0],
          [now-60000,100,105,95,100,0,1,0],
          [now,100,300,1,100,0,0,0]]
    result=m.spike_metrics([],bars,110,now)
    assert result['up_reference']['price']==95
    assert result['down_reference']['price']==105


def isolated_monitor(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'spikes.sqlite')
    return Monitor()


def test_emergency_does_not_depend_on_hourly_rule_gates(tmp_path,monkeypatch):
    monitor=isolated_monitor(tmp_path,monkeypatch)
    now=int(time.time()*1000)-1000
    store.put('settings',store.DEFAULTS|{'rule_mode':'OI且价格','min_quote':1e20,
                'cooldown_minutes':1440,'overrides':{'BTCUSDT':{'enabled':False}}})
    monitor.spike_prices['BTCUSDT'].append((now-60000,100))
    monitor.inspect_spike('BTCUSDT',110,now)
    assert len(store.spike_alerts())==1
    assert store.spike_alerts()[0][3]=='急涨'


def test_episode_is_latched_after_ack_and_across_restart(tmp_path,monkeypatch):
    monitor=isolated_monitor(tmp_path,monkeypatch)
    now=int(time.time()*1000)-2000
    monitor.spike_prices['BTCUSDT'].append((now-60000,100))
    monitor.inspect_spike('BTCUSDT',110,now)
    first=store.spike_alerts()[0]
    store.acknowledge_spikes([first[0]])
    restarted=Monitor()
    restarted.spike_prices['BTCUSDT'].append((now-60000,100))
    restarted.inspect_spike('BTCUSDT',111,now+1000)
    assert len(store.spike_alerts())==1
    assert store.spike_alerts(unacknowledged=True)==[]


def test_can_rearm_after_move_falls_below_nine_point_five(tmp_path,monkeypatch):
    monitor=isolated_monitor(tmp_path,monkeypatch)
    now=int(time.time()*1000)-4000
    monitor.spike_prices['BTCUSDT'].append((now-60000,100))
    monitor.inspect_spike('BTCUSDT',110,now)
    monitor.inspect_spike('BTCUSDT',109,now+1000)
    monitor.inspect_spike('BTCUSDT',110,now+2000)
    assert len([r for r in store.spike_alerts() if r[3]=='急涨'])==2


def test_stale_quote_cannot_raise_emergency(tmp_path,monkeypatch):
    monitor=isolated_monitor(tmp_path,monkeypatch)
    now=int(time.time()*1000)
    monitor.spike_prices['BTCUSDT'].append((now-60000,100))
    monitor.inspect_spike('BTCUSDT',200,now-30000)
    assert store.spike_alerts()==[]


def test_opposite_direction_can_alert_during_same_episode(tmp_path,monkeypatch):
    monitor=isolated_monitor(tmp_path,monkeypatch)
    now=int(time.time()*1000)-3000
    monitor.spike_prices['BTCUSDT'].append((now-60000,100))
    monitor.inspect_spike('BTCUSDT',110,now)
    monitor.inspect_spike('BTCUSDT',90,now+1000)
    assert {r[3] for r in store.spike_alerts()}=={'急涨','急跌'}


@pytest.mark.parametrize('old_window',[15,60])
def test_four_hour_window_fixed_even_when_old_setting_exists(tmp_path,monkeypatch,old_window):
    monkeypatch.setattr(store,'DB',tmp_path/'settings.sqlite')
    store.put('settings',{'window_minutes':old_window})
    assert store.settings()['window_minutes']==240
    assert m.SPIKE_WINDOW_MS==15*60000
