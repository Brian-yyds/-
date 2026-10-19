"""15m perpetual adapters for Freqtrade data and Alphalens statistics.

Factor values use closed bars only. Labels enter at the next bar open and exit
at a later open. Labels crossing the train/test split are purged.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from store import USER

STEP = pd.Timedelta(minutes=15)
FACTORS = {
    "动量 16": "过去16根收盘收益：close / close.shift(16) - 1",
    "短期反转 4": "负的过去4根收盘收益",
    "波动率 32": "过去32根收盘收益的标准差",
    "量能冲击 20": "本根成交量 / 之前20根平均成交量 - 1",
    "收盘位置": "(2×close - high - low) / (high - low)",
}


def load_histories(pairs):
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType
    result, problems = {}, []
    for pair in pairs:
        frame = load_pair_history(pair, "15m", USER / "data" / "binance",
                                  data_format="feather", candle_type=CandleType.FUTURES,
                                  fill_up_missing=False, drop_incomplete=False)
        if frame.empty:
            problems.append(f"{pair}: 没有已下载的15m永续数据")
            continue
        try:
            result[pair] = validate_history(frame)
        except ValueError as exc:
            problems.append(f"{pair}: {exc}")
    return result, problems


def validate_history(frame, now=None):
    f = frame.copy()
    f["date"] = pd.to_datetime(f["date"], utc=True)
    f = f.sort_values("date").set_index("date")
    if f.index.has_duplicates:
        raise ValueError("存在重复时间戳")
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    f = f.loc[f.index + STEP <= now]
    values = f[["open", "high", "low", "close", "volume"]].astype(float)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("包含非有限数值")
    if (values[["open", "high", "low", "close"]] <= 0).any().any() or (values.volume < 0).any():
        raise ValueError("价格或成交量非法")
    if ((values.high < values[["open", "close", "low"]].max(axis=1)) |
        (values.low > values[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC关系非法")
    if len(f) < 100:
        raise ValueError("已收盘数据少于100根")
    # Fail explicitly instead of fabricating flat candles, which bias factors.
    if not (f.index.to_series().diff().dropna() == STEP).all():
        raise ValueError("15m数据有缺口，先补齐原始数据后再研究")
    return f


def factor_value(f, name):
    if name == "动量 16":
        return f.close / f.close.shift(16) - 1
    if name == "短期反转 4":
        return -(f.close / f.close.shift(4) - 1)
    if name == "波动率 32":
        return f.close.pct_change(fill_method=None).rolling(32).std()
    if name == "量能冲击 20":
        return f.volume / f.volume.shift(1).rolling(20).mean().replace(0, np.nan) - 1
    if name == "收盘位置":
        return (2 * f.close - f.high - f.low) / (f.high - f.low).replace(0, np.nan)
    raise ValueError("未知因子")


def build_panel(histories, name, horizon=4, split=0.7):
    if horizon not in (1, 4, 16):
        raise ValueError("只支持1、4、16根标签")
    if len(histories) < 10:
        raise ValueError("横截面研究至少需要10个有完整数据的交易对")
    common = None
    for frame in histories.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 150:
        raise ValueError("共同数据区间不足150根")
    common = common.sort_values()
    cut = common[int(len(common) * split)]
    frames = []
    for asset, f in histories.items():
        # decision timestamp is close time; the source index is bar OPEN time.
        r = pd.DataFrame({"factor": factor_value(f, name),
                          "forward": f.open.shift(-(horizon + 1)) / f.open.shift(-1) - 1,
                          "exit_time": f.index.to_series().shift(-(horizon + 1))}, index=f.index)
        r = r.loc[r.index.isin(common)].dropna()
        r["date"] = r.index + STEP - pd.Timedelta(milliseconds=1)
        r["asset"] = asset
        r["segment"] = np.where(r.index >= cut, "样本外", "样本内")
        r = r.loc[~((r.index < cut) & (r.exit_time >= cut))]
        frames.append(r)
    panel = pd.concat(frames, ignore_index=True).replace([np.inf, -np.inf], np.nan).dropna()
    groups = []
    for _, group in panel.groupby("date", sort=True):
        if len(group) < 10 or group.factor.nunique() < 5:
            continue
        ranks = group.factor.rank(method="average")
        q = pd.qcut(ranks, 5, labels=False, duplicates="drop")
        if q.nunique() != 5:
            continue
        group = group.copy()
        group["factor_quantile"] = q + 1
        groups.append(group)
    if not groups:
        raise ValueError("没有满足10个交易对、5个有效分组的横截面")
    panel = pd.concat(groups).set_index(["date", "asset"]).sort_index()
    label = {1: "15m", 4: "1h", 16: "4h"}[horizon]
    panel = panel.rename(columns={"forward": label})
    # Fixed UTC grid, not every Nth observed row: missing samples cannot create overlap.
    decision_open = panel.index.get_level_values("date") + pd.Timedelta(milliseconds=1) - STEP
    keep = ((decision_open.asi8 // STEP.value) % horizon) == 0
    panel = panel.loc[keep]
    dates = panel.index.get_level_values("date")
    grid = pd.date_range(dates.min(), dates.max(), freq=STEP * horizon, name="date")
    assets = panel.index.levels[1]
    # Alphalens 0.4.6 calls asfreq(index_level.freq). Without an explicit
    # intraday frequency, pandas asfreq(None) silently downsamples to days.
    panel.index = pd.MultiIndex(levels=[grid, assets],
                               codes=[grid.get_indexer(dates), panel.index.codes[1]],
                               names=["date", "asset"])
    return panel, label, cut


def analyze_panel(panel, label, segment):
    from alphalens import performance
    subset = panel.loc[panel.segment == segment, ["factor", "factor_quantile", label]]
    if subset.empty:
        raise ValueError("该分段无有效样本")
    ic = performance.factor_information_coefficient(subset, group_adjust=False, by_group=False).dropna(subset=[label])
    means, errors = performance.mean_return_by_quantile(subset, by_date=False,
                                                        by_group=False, demeaned=False)
    if len(ic) < 20:
        raise ValueError(f"{segment}只有{len(ic)}个不重叠横截面，至少需要20个")
    turnovers = [performance.quantile_turnover(subset.factor_quantile, quantile=q, period=1)
                 for q in range(1, 6)]
    mean = float(ic[label].mean())
    std = float(ic[label].std())
    return {"ic": ic, "means": means, "errors": errors,
            "turnover": pd.concat(turnovers, axis=1), "n": len(ic),
            "mean_ic": mean, "ic_ir": mean / std if std > 0 else np.nan,
            "spread": float(means.loc[5, label] - means.loc[1, label])}


def dataset_summary(histories):
    return pd.DataFrame([{"交易对": pair, "根数": len(f), "开始 UTC": str(f.index.min()),
                          "结束 UTC": str(f.index.max())} for pair, f in histories.items()])
