import numpy as np
import pandas as pd
import pytest
import research


def histories(n=700):
    rng = np.random.default_rng(21)
    dates = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    result = {}
    for a in range(10):
        close = 100 * np.exp(np.cumsum(rng.normal(0, .003, n)))
        op = np.r_[close[0], close[:-1]]
        frame = pd.DataFrame({"date":dates,"open":op,"high":np.maximum(op,close)*1.002,
            "low":np.minimum(op,close)*.998,"close":close,"volume":rng.uniform(1,100,n)})
        result[str(a)] = research.validate_history(frame)
    return result


def test_next_open_labels_purge_and_nonoverlap():
    data = histories()
    panel, label, cut = research.build_panel(data, "动量 16", 4)
    for (date, asset), row in panel.iterrows():
        i = data[asset].index.get_loc(date + pd.Timedelta(milliseconds=1) - research.STEP)
        assert row[label] == pytest.approx(data[asset].open.iloc[i+5] / data[asset].open.iloc[i+1]-1)
        if row.segment == "样本内":
            assert row.exit_time < cut
    unique = panel.index.get_level_values("date").unique()
    assert (unique.to_series().diff().dropna() >= research.STEP*4).all()


def test_factor_prefix_unchanged_by_future_prices():
    data = histories()["0"]
    altered = data.copy()
    altered.loc[altered.index[400]:, ["open","high","low","close","volume"]] *= 2
    for name in research.FACTORS:
        pd.testing.assert_series_equal(research.factor_value(data,name).iloc[:400], research.factor_value(altered,name).iloc[:400])


def test_gap_is_rejected():
    data = histories()["0"].reset_index().drop(index=20)
    with pytest.raises(ValueError,match="缺口"):
        research.validate_history(data)


def test_incomplete_bar_excluded():
    data = histories()["0"].reset_index()
    now = data.date.iloc[-1] + pd.Timedelta(minutes=5)
    result = research.validate_history(data,now=now)
    assert len(result) == len(data)-1


@pytest.mark.parametrize("horizon", [1,4,16])
def test_alphalens_real_adapter(horizon):
    panel,label,_ = research.build_panel(histories(1400),"动量 16",horizon)
    report = research.analyze_panel(panel,label,"样本外")
    assert report["n"] >= 20
    assert report["n"] == panel.loc[panel.segment == "样本外"].index.get_level_values("date").nunique()
    assert -1 <= report["mean_ic"] <= 1
    assert list(report["means"].index) == [1,2,3,4,5]


def test_constant_factors_do_not_create_artificial_quantiles():
    data = histories()
    for frame in data.values():
        frame["close"] = 100.
    with pytest.raises(ValueError,match="有效分组"):
        research.build_panel(data,"动量 16",4)
