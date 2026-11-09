"""15-minute excursions, restricted to fresh 24h USDT-turnover top 20."""
import math
import time
import store

TOP=20
THRESHOLD=5.
POLICY='24h成交额前20'


def top_symbols(tickers,universe,now=None):
    now=time.time() if now is None else now
    values=[]
    for symbol in set(universe):
        tick=tickers.get(symbol,{})
        volume=tick.get('quote24h');price=tick.get('price');event=tick.get('event_time',0)
        if (isinstance(volume,(int,float)) and not isinstance(volume,bool) and math.isfinite(volume) and volume>0
            and isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price>0 and 0<=now*1000-event<=90000):
            values.append((symbol,volume))
    return [s for s,v in sorted(values,key=lambda x:(-x[1],x[0]))[:TOP]]


def snapshot(now=None):
    now=time.time() if now is None else now
    ticks=store.get('live_market',{}).get('tickers',{})
    universe=store.get('exchange_info',{}).get('market_symbols',[])
    top=top_symbols(ticks,universe,now)
    since=store.get('spike_top20_since',now-86400)
    # First event anchors tracking permanently. New directions/episodes for the
    # same symbol remain in the event ledger but never add a duplicate card.
    with store.connect() as con:
        records=con.execute('''SELECT a.id,a.time,a.symbol,a.direction,a.change,a.base_price,a.price,
            a.base_time,a.event_time,a.basis,a.acknowledged FROM spike_alerts a JOIN
            (SELECT MIN(id) AS id FROM spike_alerts WHERE time>=? AND instr(basis,?)>0 GROUP BY symbol) first
            ON a.id=first.id ORDER BY a.time DESC,a.id DESC LIMIT 100''',(since,POLICY)).fetchall()
    symbols=[r[2] for r in records]
    events={s:ticks[s]['event_time'] for s in symbols if ticks.get(s,{}).get('event_time')}
    baselines=store.price_baselines(events,15)
    _,bars=store.live_inputs(symbols,now,include_oi=False)
    tracked=[]
    for r in records:
        ident,triggered,symbol,direction,change,base_price,trigger_price,base_time,event_time,basis,ack=r
        tick=ticks.get(symbol,{})
        price=tick.get('price');event=tick.get('event_time',0)
        fresh=(isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price)
               and price>0 and 0<=now*1000-event<=20000)
        base=baselines.get(symbol);window_basis='实时价格采样'
        if base is None and event:
            target=(event-15*60000)//60000*60000
            candle=next((b for b in bars.get(symbol,[]) if b[0]==target),None)
            base=(candle[0],candle[1]) if candle else None
            window_basis='15分钟前所在分钟开盘价'
        reference_valid=isinstance(base_price,(int,float)) and math.isfinite(base_price) and base_price>0
        tracked.append(dict(id=ident,symbol=symbol,triggered=triggered,trigger_change=change,
            trigger_price=trigger_price,trigger_direction=direction,base_price=base_price,base_time=base_time,
            price=price if fresh else None,event_time=event,fresh=fresh,in_top20=symbol in top,
            tracking_change=(price/base_price-1)*100 if fresh and reference_valid else None,
            change15m=(price/base[1]-1)*100 if fresh and base and base[1]>0 else None,
            base15m_time=base[0] if base else None,window_basis=window_basis,basis=basis))
    return dict(generated_at=now,universe=top,rank_window='24h',rank_field='quoteVolume',quote_currency='USDT',
                top_n=TOP,window_minutes=15,threshold_pct=THRESHOLD,strict=True,
                tracking_basis='first_alert_reference',display_dedup='symbol',records=records,tracked=tracked)
