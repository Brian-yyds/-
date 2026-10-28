"""Fifteen Beijing calendar days: crypto-market and daily top-ten turnover."""
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import store
from collector import PublicClient
from market_scanner import crypto_contracts

ZONE=timezone(timedelta(hours=8))
HOUR=3600000
DAY=24*HOUR
PAUSE=300
DAYS=15
SNAPSHOT_KEY='market_volume_15d'


def window_start(now):
    midnight=datetime.fromtimestamp(now,ZONE).replace(hour=0,minute=0,second=0,microsecond=0)
    return int((midnight-timedelta(days=DAYS-1)).timestamp()*1000)


def schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS market_hourly_volume (symbol TEXT,t INTEGER,quote REAL,observed REAL,PRIMARY KEY(symbol,t))')


def expected_hours(onboard,start,now):
    first=max(start,int(onboard)//HOUR*HOUR)
    return range(first,int(now*1000)//HOUR*HOUR+1,HOUR)


def parse_rows(rows,start,now):
    if not isinstance(rows,list): raise ValueError('成交额格式异常')
    result={}
    for row in rows:
        if not isinstance(row,list) or len(row)<8: raise ValueError('成交额K线字段不足')
        stamp=int(row[0]);quote=float(row[7])
        if stamp%HOUR or int(row[6])!=stamp+HOUR-1 or not math.isfinite(quote) or quote<0:
            raise ValueError('成交额K线无效')
        if start<=stamp<=int(now*1000)//HOUR*HOUR:
            if stamp in result: raise ValueError('成交额K线重复')
            result[stamp]=quote
    return result


def aggregate(symbols,cache,start,now):
    """No market total is published if a required listed hour is absent."""
    per_symbol={}
    missing=[]
    for symbol,onboard in symbols.items():
        values=cache.get(symbol,{})
        wanted=expected_hours(onboard,start,now)
        if any(t not in values for t in wanted):
            missing.append(symbol)
            continue
        daily=[0.]*DAYS
        for t in wanted:
            quote=values[t]
            if not math.isfinite(quote) or quote<0: raise ValueError('缓存成交额无效')
            daily[(t-start)//DAY]+=quote
        per_symbol[symbol]=daily
    days=[]
    for i in range(DAYS):
        ranked=sorted(((symbol,values[i]) for symbol,values in per_symbol.items() if values[i]>0),key=lambda r:(-r[1],r[0]))
        leaders=[dict(symbol=symbol,quote=quote) for symbol,quote in ranked[:10]]
        days.append(dict(start=start+i*DAY,quote=math.fsum(values[i] for values in per_symbol.values()),
                         top10_quote=math.fsum(t['quote'] for t in leaders),top10=leaders))
    return dict(complete=not missing,missing=missing,
                days=days,total=math.fsum(d['quote'] for d in days) if not missing else None,symbol_count=len(symbols),
                range_start=start,range_end=int(now*1000),includes_today=True,timezone='Asia/Shanghai')


class Monitor:
    def __init__(self):
        self.local=threading.local()
        self.gate=threading.Lock()
        self.next_request=0
        self.blocked_until=0

    def get(self,path,**params):
        with self.gate:
            due=max(time.monotonic(),self.next_request)
            self.next_request=due+.3  # <=3.34 requests/s; bootstrap <=6.7 weight/s.
        delay=due-time.monotonic()
        if delay>0: time.sleep(delay)
        blocked=max(self.blocked_until,store.get('monitor_health',{}).get('blocked_until',0))
        if time.time()<blocked: raise RuntimeError('等待币安限流恢复')
        if not hasattr(self.local,'client'): self.local.client=PublicClient()
        client=self.local.client
        try: return client.get(path,**params)
        finally: self.blocked_until=max(self.blocked_until,client.blocked_until)

    def cycle(self):
        started=time.time()
        start=window_start(started)
        info=self.get('/fapi/v1/exchangeInfo')
        selected=set(crypto_contracts(info))  # Includes BTC; independent of ranking exclusions.
        symbols={s['symbol']:max(0,int(s.get('onboardDate') or 0)) for s in info['symbols']
                 if s['symbol'] in selected}
        if not symbols: raise ValueError('尚无可用合约清单')
        with store.connect() as con:
            schema(con)
            rows=con.execute('SELECT symbol,t,quote FROM market_hourly_volume WHERE t>=?',(start,)).fetchall()
        cache={}
        for symbol,t,quote in rows: cache.setdefault(symbol,{})[t]=quote
        done=0;failures=[]
        store.put('market_volume_health',dict(heartbeat=started,status='正在汇总',completed=0,total=len(symbols)))

        def sample(symbol):
            now=time.time()
            wanted=list(expected_hours(symbols[symbol],start,now))
            if not wanted: return symbol,{}
            missing=next((t for t in wanted if t not in cache.get(symbol,{})),wanted[-1])
            first=min(missing,max(wanted[0],wanted[-1]-HOUR))
            limit=min(499,max(2,(int(now*1000)//HOUR*HOUR-first)//HOUR+2))
            raw=self.get('/fapi/v1/klines',symbol=symbol,interval='1h',startTime=first,limit=limit)
            values=parse_rows(raw,start,time.time())
            with store.connect() as con:
                con.executemany('INSERT OR REPLACE INTO market_hourly_volume VALUES (?,?,?,?)',
                                [(symbol,t,q,time.time()) for t,q in values.items()])
            return symbol,values

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(sample,s):s for s in symbols}
            for future in as_completed(futures):
                try:
                    symbol,values=future.result()
                    cache.setdefault(symbol,{}).update(values)
                except Exception:
                    failures.append(futures[future])
                done+=1
                if done%20==0 or done==len(symbols):
                    store.put('market_volume_health',dict(heartbeat=time.time(),status='正在汇总',
                              completed=done,total=len(symbols),failed=len(failures)))
        finished=time.time()
        # A midnight rollover must never label yesterday's window as today's.
        if window_start(finished)!=start: return 1
        # Freeze the expected last hour to the cycle's start. If an hour rolls
        # over while scanning, refresh it next cycle rather than fabricate gaps.
        result=aggregate(symbols,cache,start,started)
        result.update(updated_at=finished,scan_started=started,source='Binance /fapi/v1/klines quote asset volume',
                      scope='当前在交易的USDT加密永续，含BTC/ETH，不含股票、现货或币本位')
        if result['complete'] and not failures:
            store.put(SNAPSHOT_KEY,result)
        with store.connect() as con:
            con.execute('DELETE FROM market_hourly_volume WHERE t<?',(start-DAY,))
        good=result['complete'] and not failures
        store.put('market_volume_health',dict(heartbeat=finished,status='统计完成' if good else '部分数据待补齐',
                  completed=done,total=len(symbols),failed=len(failures),missing=len(result['missing']),
                  blocked_until=self.blocked_until))
        return PAUSE if good else max(60,min(900,self.blocked_until-finished))


def run():
    monitor=Monitor()
    while True:
        try: delay=monitor.cycle()
        except Exception:
            store.put('market_volume_health',dict(heartbeat=time.time(),status='成交统计暂不可用，稍后重试',
                                                 blocked_until=monitor.blocked_until))
            delay=max(60,min(900,monitor.blocked_until-time.time()))
        time.sleep(max(1,delay))


if __name__=='__main__': run()
