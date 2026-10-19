import pytest
import store
import monitoring as m


def test_oi_uses_quantity_not_price():
    now=10_000_000
    result=m.oi_metrics([[now-900000,100,"live"],[now,110,"live"]],now,15)
    assert result["oi_change"]==pytest.approx(10)
    assert result["oi_basis"]=="实时采样"


def test_bootstrap_is_labeled_and_actual_span_visible():
    now=10_000_000
    result=m.oi_metrics([[now-1100000,100,"history5m"],[now,110,"live"]],now,15)
    assert result["oi_basis"]=="5m历史快照"
    assert result["oi_span"]==pytest.approx(1100000/60000)


def test_live_baseline_preferred_over_closer_historical_snapshot():
    now=10_000_000
    result=m.oi_metrics([[now-910000,100,"live"],[now-900000,90,"history5m"],[now,110,"live"]],now,15)
    assert result["oi_basis"]=="实时采样"
    assert result["oi_change"]==pytest.approx(10)


def test_stale_or_missing_oi_not_faked():
    assert m.oi_metrics([],10_000_000,15)["oi_change"] is None
    assert m.oi_metrics([[1000,100,"live"]],10_000_000,15)["oi"] is None


def minutes(now,window=5):
    current=now//60000*60000
    return [[t,100,105,99,102,200 if t>=current-window*60000 else 100,int(t<current),now/1000]
            for t in range(current-2*window*60000,current+1,60000)]


def test_quote_sum_is_actual_quote_not_base_volume_or_24h_difference():
    now=12_030_000
    result=m.minute_metrics(minutes(now),103,now,5)
    assert result["quote"]==1000
    assert result["quote_ratio"]==2
    assert result["price_change"]==pytest.approx(3)


def test_minute_gaps_and_stale_partial_are_excluded():
    now=12_030_000
    assert m.minute_metrics(minutes(now)[-3:],103,now,5)["quote"] is None
    old=minutes(now)
    old[-1][7]-=30
    assert m.minute_metrics(old,103,now,5)["price_change"] is None


def row():
    return {"price_fresh":True,"quote":100000,"price_change":2,"oi_change":4,
            "oi_basis":"实时采样","quote_ratio":3,"priority":True,"symbol":"BTCUSDT"}


def test_custom_rules_and_and_mode():
    cfg=store.DEFAULTS|{"overrides":{"BTCUSDT":{"up_pct":5}}}
    rule=m.effective_rule(cfg,"BTCUSDT")
    assert "上涨" not in m.evaluate(row(),rule)
    assert m.evaluate(row(),rule|{"rule_mode":"OI且价格"})==[]
    assert set(m.evaluate(row(),m.effective_rule(store.DEFAULTS,"BTCUSDT")|{"rule_mode":"OI且价格"}))=={"上涨","OI增仓"}


def test_history_cannot_trigger_live_oi_alert_and_volume_gate_applies():
    rule=m.effective_rule(store.DEFAULTS,"BTCUSDT")
    assert "OI增仓" not in m.evaluate(row()|{"oi_basis":"5m历史快照"},rule)
    assert m.evaluate(row(),rule|{"min_quote":200000})==[]
    assert m.evaluate(row()|{"price_fresh":False},rule)==[]
    assert m.evaluate(row(),rule|{"enabled":False})==[]


def test_rank_directions_do_not_mix_gains_and_losses():
    rows=[row(),row()|{"symbol":"ETHUSDT","oi_change":-5,"price_change":-3},row()|{"symbol":"SOLUSDT","price_fresh":False}]
    assert [r["symbol"] for r in m.ranked(rows,"oi_up")]==["BTCUSDT"]
    assert [r["symbol"] for r in m.ranked(rows,"down")]==["ETHUSDT"]


def test_history_seed_never_overwrites_live_sample(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB",tmp_path/"live.sqlite")
    store.save_oi("BTCUSDT",[(100000,110,"live")])
    store.save_oi("BTCUSDT",[(100000,100,"history5m")])
    with store.connect() as con:
        assert con.execute("SELECT amount,source FROM oi_samples").fetchone()==(110,"live")
