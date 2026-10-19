import pytest
import collector
import store


def rows():
    return [[i*900000,"100","102","99",str(100+i),"10",(i+1)*900000-1] for i in range(25)]


def test_metrics_use_closed_candles_only():
    data = rows()
    result = collector.closed_metrics(data,24*900000+300000)
    assert result["close"] == 123
    assert result["move15m_pct"] == pytest.approx((123/122-1)*100)
    assert result["volume_ratio"] == 1


def test_stale_and_gaps_do_not_trigger():
    with pytest.raises(ValueError,match="过期"):
        collector.closed_metrics(rows(),28*900000)
    with pytest.raises(ValueError,match="缺口"):
        collector.closed_metrics(rows()[:20]+rows()[21:],25*900000)


def test_alert_cooldown_and_persistence(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB",tmp_path/"test.sqlite")
    assert store.record_alert("BTCUSDT","move",2,True,"test",900,now=1000)
    assert not store.record_alert("BTCUSDT","move",2,True,"test",900,now=1100)
    assert store.record_alert("BTCUSDT","move",2,True,"test",900,now=1900)
    assert len(store.recent_alerts()) == 2
