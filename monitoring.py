"""Rule evaluation and rankings use observed public data only."""
import time
import math
import store

RULE_WINDOW_MINUTES=store.RANK_WINDOW_MINUTES
SPIKE_WINDOW_MS=15*60*1000
SPIKE_THRESHOLD_PCT=10.0


def focus_quotes(symbols, now=None):
    """Same rolling four-hour baseline as the rankings, including excluded BTC."""
    now=time.time() if now is None else now
    ticks=store.get('live_market',{}).get('tickers',{})
    events={s:ticks[s]['event_time'] for s in symbols if ticks.get(s,{}).get('event_time')}
    baselines=store.price_baselines(events,RULE_WINDOW_MINUTES)
    _,bars=store.live_inputs(symbols,now,include_oi=False)
    result={}
    for symbol in symbols:
        tick=ticks.get(symbol,{})
        price=tick.get('price')
        event=tick.get('event_time',0)
        fresh=isinstance(price,(int,float)) and math.isfinite(price) and price>0 and 0<=now*1000-event<=20000
        base=baselines.get(symbol)
        basis='实时价格采样'
        if base is None:
            target=(event-RULE_WINDOW_MINUTES*60000)//60000*60000
            candle=next((b for b in bars.get(symbol,[]) if b[0]==target),None)
            base=(candle[0],candle[1]) if candle else None
            basis='4小时前所在分钟开盘价'
        change=(price/base[1]-1)*100 if fresh and base and base[1]>0 else None
        result[symbol]=dict(price=price,event_time=event,fresh=fresh,change=change,basis=basis)
    return result


def spike_metrics(observations,bars,price,event_time):
    """Any rise/fall of 10% within <=15m, independent of hourly rules.

    Live observations preserve sub-minute moves. Complete historical 1m bars
    wholly inside the lookback bootstrap their highs/lows; their exact extreme
    timestamps are unknown, so base_time is an upper bound on elapsed time.
    """
    if not math.isfinite(price) or price<=0:
        return None
    start=event_time-SPIKE_WINDOW_MS
    candidates=[{"price":float(p),"time":int(t),"basis":"实时价格"} for t,p in observations
                if start<=t<event_time and math.isfinite(p) and p>0]
    for b in bars:
        t=int(b[0])
        if b[6] and start<=t and t+59999<=event_time:
            for value,label in [(b[2],"1m已收盘高点"),(b[3],"1m已收盘低点")]:
                if math.isfinite(value) and value>0:
                    candidates.append({"price":float(value),"time":t,"basis":label})
    if not candidates:
        return None
    low=min(candidates,key=lambda x:x['price'])
    high=max(candidates,key=lambda x:x['price'])
    return {"up_pct":(price/low['price']-1)*100,"down_pct":(price/high['price']-1)*100,
            "up_reference":low,"down_reference":high}


def minute_metrics(bars, price, now_ms, window):
    # Latest N one-minute candles including the current, unfinished minute.
    current_minute = now_ms // 60000 * 60000
    start = current_minute - (window-1)*60000
    by_t = {int(r[0]): r for r in bars}
    wanted = list(range(start, current_minute+1, 60000))
    if any(t not in by_t for t in wanted) or now_ms/1000-by_t.get(current_minute,[0]*8)[7]>25:
        return {"price_change":None,"quote":None,"quote_ratio":None,"minute_ready":False}
    quote = sum(by_t[t][5] for t in wanted)
    # Do NOT pretend we know historical intra-minute volume. Use completed
    # windows for the volume-spike ratio, while the displayed quote includes live volume.
    completed = list(range(current_minute-window*60000, current_minute, 60000))
    prev_completed = list(range(current_minute-2*window*60000, current_minute-window*60000, 60000))
    ratio = None
    if all(t in by_t and by_t[t][6] for t in completed+prev_completed):
        denom = sum(by_t[t][5] for t in prev_completed)
        if denom > 0:
            ratio = sum(by_t[t][5] for t in completed)/denom
    baseline=by_t.get(current_minute-window*60000)
    return {"price_change":(price/baseline[1]-1)*100 if baseline else None,"quote":quote,
            "quote_ratio":ratio,"minute_ready":True}


def oi_metrics(samples, now_ms, window, max_age_ms=40000):
    live = [r for r in samples if r[2] == "live" and 0 <= now_ms-r[0] <= max_age_ms]
    if not live:
        return {"oi":None,"oi_change":None,"oi_span":None,"oi_basis":"等待实时OI","oi_time":None}
    latest = max(live, key=lambda r:r[0])
    target = latest[0]-window*60000
    baselines = [r for r in samples if r[0] <= target and target-r[0] <= (max(45000,max_age_ms) if r[2]=="live" else 330000)]
    if not baselines:
        return {"oi":latest[1],"oi_change":None,"oi_span":None,"oi_basis":"积累基准中","oi_time":latest[0]}
    live_baselines = [r for r in baselines if r[2]=="live"]
    base = max(live_baselines or baselines, key=lambda r:r[0])
    return {"oi":latest[1],"oi_change":(latest[1]/base[1]-1)*100 if base[1]>0 else None,
            "oi_span":(latest[0]-base[0])/60000,
            "oi_basis":"实时采样" if base[2]=="live" else "5m历史快照",
            "oi_time":latest[0]}


def effective_rule(cfg, symbol):
    rule = cfg | cfg.get("overrides", {}).get(symbol, {})
    rule["enabled"] = rule.get("enabled", True)
    return rule


def evaluate(row, rule):
    if not rule["enabled"] or not row["price_fresh"]:
        return []
    if row["quote"] is None or row["quote"] < rule["min_quote"]:
        return []
    price_hits, oi_hits, other = [], [], []
    change = row["price_change"]
    if change is not None:
        if change >= rule["up_pct"]: price_hits.append("上涨")
        if change <= -rule["down_pct"]: price_hits.append("下跌")
    oi = row["oi_change"]
    # Historical 5m bootstrap can rank immediately, but alerts require a real
    # sampled baseline within 45s of the exact target to avoid false triggers.
    if oi is not None and row["oi_basis"] == "实时采样":
        if oi >= rule["oi_up_pct"]: oi_hits.append("OI增仓")
        if oi <= -rule["oi_down_pct"]: oi_hits.append("OI减仓")
    if row["quote_ratio"] is not None and row["quote_ratio"] >= rule["quote_ratio"]:
        other.append("成交额放大")
    if rule["rule_mode"] == "OI且价格":
        return oi_hits+price_hits if oi_hits and price_hits else []
    return price_hits+oi_hits+other


def market_symbols(cfg=None):
    cfg=store.settings() if cfg is None else cfg
    info=store.get('exchange_info',{})
    symbols=store.filter_stock_symbols(info.get('market_symbols',info.get('alt_symbols',info.get('symbols',[]))),cfg,info)
    return [s for s in symbols if s not in cfg.get('market_excluded',['BTCUSDT'])]


def monitored_spike_alerts(records,cfg=None):
    allowed=set(store.filter_stock_symbols([r[2] for r in records],cfg))
    return [r for r in records if r[2] in allowed]


def build_rows(cfg=None, now=None, scope='focus'):
    cfg = (store.settings() if cfg is None else cfg) | {"window_minutes":RULE_WINDOW_MINUTES}
    now = time.time() if now is None else now
    snapshot = store.get("live_market", {})
    ticks = snapshot.get("tickers", {})
    info=store.get('exchange_info',{})
    stocks = set(info.get('stock_symbols',[]))
    commodities = set(info.get('commodity_symbols',[]))
    symbols=market_symbols(cfg) if scope=='market' else store.filter_stock_symbols(cfg['watchlist'],cfg)
    oi_map,bars_map = store.live_inputs(symbols, now,oi_window_minutes=cfg['window_minutes'])
    baselines=store.price_baselines({s:ticks.get(s,{}).get('event_time',int(now*1000)) for s in symbols},RULE_WINDOW_MINUTES)
    import price_history
    three_day_bases=price_history.baselines({s:ticks.get(s,{}).get('event_time',0) for s in symbols})
    rows = []
    for symbol in symbols:
        tick = ticks.get(symbol, {})
        price = tick.get("price")
        fresh = bool(price and 0 <= now*1000-tick.get("event_time",0) <= (90000 if scope=='market' else 20000))
        row = {"symbol":symbol,"asset_type":'stock' if symbol in stocks else 'commodity' if symbol in commodities else 'crypto',"priority":symbol in cfg["priority"],"price":price,
               "price_fresh":fresh,"price_time":tick.get("event_time"),
               "change24h":tick.get("change24h"),"quote24h":tick.get("quote24h"),
               "mark":tick.get("mark",price)}
        row.update(minute_metrics(bars_map.get(symbol,[]),price, tick.get('event_time',int(now*1000)),cfg["window_minutes"])
                   if fresh else {"price_change":None,"quote":None,"quote_ratio":None,"minute_ready":False})
        base=baselines.get(symbol)
        if not base:
            target=tick.get('event_time',int(now*1000))-cfg['window_minutes']*60000
            baseline=next((b for b in bars_map.get(symbol,[]) if b[0]==target//60000*60000),None)
            base=(baseline[0],baseline[1]) if baseline else None
            row['price_basis']='4小时前所在分钟开盘价' if base else '历史数据待补齐'
        else: row['price_basis']='实时价格采样（5秒桶）'
        row['base_time'],row['base_price']=base if base else (None,None)
        row['price_span']=(tick['event_time']-base[0])/60000 if base and fresh else None
        row['price_change']=(price/base[1]-1)*100 if fresh and base and base[1]>0 else None
        base_3d=three_day_bases.get(symbol)
        row['price_change_3d']=(price/base_3d[1]-1)*100 if fresh and base_3d else None
        row['base_3d_time'],row['base_3d_price']=base_3d if base_3d else (None,None)
        row['price_3d_basis']='72小时前所在分钟开盘价' if base_3d else '72小时历史基准待补齐 / 未上市或休市'
        row.update(oi_metrics(oi_map.get(symbol,[]),int(now*1000),cfg["window_minutes"],180000 if scope=='market' and symbol not in cfg['watchlist'] else 40000))
        row["oi_usdt"] = row["oi"]*row["mark"] if row["oi"] is not None and fresh else None
        row["hits"] = evaluate(row,effective_rule(cfg,symbol))
        rows.append(row)
    return rows


def ranked(rows, kind, priority_only=False):
    rows = [r for r in rows if r["price_fresh"] and (not priority_only or r["priority"])]
    if kind in ("oi_up","oi_down"):
        rows = [r for r in rows if r["oi_change"] is not None and (r["oi_change"]>0 if kind=="oi_up" else r["oi_change"]<0)]
        return sorted(rows,key=lambda r:r["oi_change"],reverse=kind=="oi_up")
    rows = [r for r in rows if r["price_change"] is not None and (r["price_change"]>0 if kind=="up" else r["price_change"]<0)]
    return sorted(rows,key=lambda r:r["price_change"],reverse=kind=="up")
