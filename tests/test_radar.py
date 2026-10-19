import json
import time
import pytest
import radar
import store
from live_collector import Monitor


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'radar.sqlite')


def trade(ident,t,m=False,price=100,qty=2):
    return dict(a=ident,T=t,m=m,p=str(price),q=str(qty))


def test_aggressor_side_and_duplicates_do_not_inflate_flow():
    f=radar.FlowTracker()
    f.add(trade(1,1000))
    f.add(trade(1,1000))
    f.add(trade(2,2000,True,qty=3))
    r=f.metrics(2000)
    assert (r['buy5'],r['sell5'],r['delta5'])==(200,300,-100)
    assert not r['ready']


def test_gap_invalidates_coverage_and_old_flow_is_not_reused():
    f=radar.FlowTracker()
    f.add(trade(1,1000))
    f.add(trade(2,301000))
    assert f.metrics(301000)['ready']
    f.add(trade(4,302000,True))
    assert not f.metrics(302000)['ready']
    assert f.metrics(302000)['buy5']==0
    assert not f.metrics(331000)['fresh']


def test_rolling_flow_excludes_older_than_window():
    f=radar.FlowTracker()
    f.add(trade(1,1000))
    f.add(trade(2,302000,True))
    r=f.metrics(302000)
    assert r['delta5']==-200
    assert r['imbalance5']==-100


def raw_depth(now=1000,partial=False):
    return dict(E=now*1000,bids=[['99.99','100'],['99.8','1000']],
                asks=[['100.01','100'],['100.2','1000']] if not partial else [['100.01','100']])


def test_depth_band_and_sweep_are_not_notional_sum_averages():
    m=radar.depth_metrics(raw_depth(),10,15000,1000)
    assert m['band_complete']
    assert m['bid_quote']==9999
    expected=15000/(100+(15000-10001)/100.2)
    assert m['buy_sweep']['vwap']==pytest.approx(expected)
    assert m['buy_sweep']['slippage_bps']>1


def test_partial_book_does_not_fabricate_imbalance_or_full_fill():
    m=radar.depth_metrics(raw_depth(partial=True),10,50000,1000)
    assert not m['band_complete']
    assert m['imbalance'] is None
    assert not m['buy_sweep']['complete']
    assert m['buy_sweep']['slippage_bps'] is None
    assert not radar.depth_metrics(raw_depth(),10,10000,1100)['fresh']


def test_crossed_book_is_rejected():
    raw=raw_depth()
    raw['asks']=[['99','10']]
    with pytest.raises(ValueError): radar.depth_metrics(raw,now=1000)


def test_depth_drop_requires_comparable_time_spanning_baseline(isolated):
    for now in [1000,1030,1060,1090]:
        radar.save_depth('X',radar.depth_metrics(raw_depth(now),now=now))
    raw=raw_depth(1120)
    raw['bids'][0][1]='50'
    last=radar.depth_metrics(raw,now=1120)
    radar.save_depth('X',last)
    assert last['bid_drop_pct']==pytest.approx(50)
    different=radar.depth_metrics(raw_depth(1150),band_bps=15,now=1150)
    radar.save_depth('X',different)
    assert different['bid_drop_pct'] is None


def fixture_evidence():
    r=dict(symbol='X',price_fresh=True,price_change=1.,quote=2e6,quote_ratio=2.,
           oi_basis='实时采样',oi_change=3.,priority=True,hits=[])
    f=dict(ready=True,buy5=700000,sell5=300000,delta5=400000,imbalance5=40.,change5=.2,max_sell5=10000)
    b=radar.depth_metrics(raw_depth(),now=1000)
    return r,f,b


def test_early_candidate_stays_observation_and_needs_live_oi():
    r,f,b=fixture_evidence()
    assert radar.assess(r,f,b,store.DEFAULTS)['kind']=='蓄势候选'
    assert radar.assess(r|{'oi_basis':'5m历史快照'},f,b,store.DEFAULTS)['kind'] is None
    assert radar.assess(r,f|{'ready':False},b,store.DEFAULTS)['level']=='数据待齐'


def test_dump_requires_sell_flow_and_liquidity_evidence():
    r,f,b=fixture_evidence()
    r['price_change']=-2
    f.update(imbalance5=-60.,change5=-1.5)
    assert radar.assess(r,f,b,store.DEFAULTS)['kind'] is None
    b['bid_drop_pct']=40
    assert radar.assess(r,f,b,store.DEFAULTS)['kind']=='抛压异动确认'
    assert radar.assess(r,f|{'imbalance5':60},b,store.DEFAULTS)['kind']!='抛压异动确认'
    assert not radar.assess(r,f,b,store.DEFAULTS|{'radar_min_quote':1e9})['eligible']


def test_persistent_condition_alerts_once_and_rearms_after_release_and_cooldown(isolated):
    cfg=store.DEFAULTS|dict(confirm_seconds=30,release_seconds=60,cooldown_minutes=1)
    assert radar.advance('X','dump',True,{},cfg,1000) is None
    assert radar.advance('X','dump',True,{},cfg,1020) is None
    assert radar.advance('X','dump',True,{},cfg,1030)
    assert radar.advance('X','dump',True,{},cfg,1060) is None
    assert radar.advance('X','dump',True,{},cfg,1100) is None
    radar.advance('X','dump',False,{},cfg,1120)
    radar.advance('X','dump',True,{},cfg,1130)
    assert radar.advance('X','dump',True,{},cfg,1160) is None
    radar.advance('X','dump',False,{},cfg,1220)
    radar.advance('X','dump',True,{},cfg,1230)
    assert radar.advance('X','dump',True,{},cfg,1260)
    assert len(radar.events())==2


def test_sampling_gap_does_not_count_as_continuous_confirmation(isolated):
    cfg=store.DEFAULTS|{'confirm_seconds':30}
    radar.advance('X','up',True,{},cfg,1000)
    assert radar.advance('X','up',True,{},cfg,1060) is None
    assert radar.events()==[]


def test_emergency_threshold_hot_change_is_independent_and_auditable(isolated):
    store.put('settings',store.DEFAULTS|dict(spike_up_pct=15.,spike_down_pct=5.))
    m=Monitor()
    now=int(time.time()*1000)-5000
    m.spike_prices['X'].append((now-60000,100))
    m.inspect_spike('X',110,now)
    assert not store.spike_alerts()
    store.put('settings',store.settings()|{'spike_up_pct':8.})
    m.spike_config_time=0
    m.inspect_spike('X',110,now+1000)
    records=store.spike_alerts()
    assert len(records)==1 and '阈值8%' in records[0][9]
    m.inspect_spike('X',94,now+2000)
    assert {r[3] for r in store.spike_alerts()}=={'急涨','急跌'}


def test_stale_flows_and_books_are_excluded_at_read_time(isolated):
    store.put('order_flow',{'X':dict(ready=True,computed_at=1000,last_time=1000000)})
    radar.save_depth('X',radar.depth_metrics(raw_depth(),now=1000))
    f,b=radar.inputs(['X'],1100)
    assert not f['X']['ready'] and not b['X']['fresh']


def test_parameter_changes_wait_for_new_depth_and_reset_pending_confirmation(isolated):
    r,f,b=fixture_evidence()
    assert '等待按新参数' in ' '.join(radar.assess(r,f,b,store.DEFAULTS|{'depth_band_bps':20})['missing'])
    cfg=store.DEFAULTS|{'confirm_seconds':30}
    radar.advance('X','up',True,{'rule_id':'old'},cfg,1000)
    assert radar.advance('X','up',True,{'rule_id':'new'},cfg,1030) is None
    assert radar.advance('X','up',True,{'rule_id':'new'},cfg,1060)
    assert radar.advance('X','up',True,{'rule_id':'newer'},cfg,1090) is None
    assert len(radar.events())==1


def test_single_coin_volume_gate_and_rule_snapshot_are_preserved(isolated):
    r,f,b=fixture_evidence()
    cfg=store.DEFAULTS|{'overrides':{'X':{'min_quote':1e8}}}
    decision=radar.assess(r,f,b,cfg)
    assert not decision['eligible']
    data=radar.snapshot(r,f,b,decision,cfg=cfg)
    assert data['rules_at_capture']['min_quote']==1e8
    assert data['flow_1h']['delta1h'] is None
