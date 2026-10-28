"""Bounded all-market bootstrap/OI sampling, independent of the focus-pool loop."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import store
import monitoring
from collector import PublicClient

STOCK_UNDERLYING_TYPES = frozenset({'EQUITY', 'KR_EQUITY', 'HK_EQUITY', 'CN_EQUITY', 'PREMARKET'})


def stock_contracts(info):
    return sorted(s['symbol'] for s in info['symbols']
                  if s.get('contractType') in ('PERPETUAL','TRADIFI_PERPETUAL') and s.get('status') == 'TRADING'
                  and s.get('quoteAsset') == 'USDT' and s.get('marginAsset') == 'USDT'
                  and s.get('underlyingType') in STOCK_UNDERLYING_TYPES)


def commodity_contracts(info):
    return sorted(s['symbol'] for s in info['symbols']
                  if s.get('contractType') in ('PERPETUAL','TRADIFI_PERPETUAL') and s.get('status')=='TRADING'
                  and s.get('quoteAsset')=='USDT' and s.get('marginAsset')=='USDT'
                  and s.get('underlyingType')=='COMMODITY')


def monitored_contracts(info):
    return sorted(set(crypto_contracts(info)) | set(stock_contracts(info)) | set(commodity_contracts(info)))


def crypto_contracts(info):
    return sorted(s['symbol'] for s in info['symbols']
                  if s.get('contractType')=='PERPETUAL' and s.get('status')=='TRADING'
                  and s.get('quoteAsset')=='USDT' and s.get('marginAsset')=='USDT'
                  and s.get('underlyingType')=='COIN'
                  and ('Crypto' in s.get('underlyingSubType',[]) or not s.get('underlyingSubType')))


class MarketScanner:
    def __init__(self, monitor):
        self.monitor=monitor
        self.local=threading.local()
        self.gate=threading.Lock()
        self.next_request=0
        self.next_weight=0
        self.oi_due={}
        self.retry={}
        self.errors={}
        self.startup_until=time.time()+20
        current=int(time.time()*1000)//60000*60000
        with store.connect() as con:
            count=2*store.RANK_WINDOW_MINUTES+1
            ready=con.execute('SELECT symbol FROM minute_candles WHERE t BETWEEN ? AND ? GROUP BY symbol HAVING COUNT(*)=?',
                              (current-(count-1)*60000,current,count)).fetchall()
            recent=con.execute("SELECT symbol,MAX(t) FROM oi_samples WHERE source='live' GROUP BY symbol").fetchall()
        self.monitor.seeded.update({s:time.time() for (s,) in ready})
        self.oi_due.update({s:t/1000 for s,t in recent})

    def get(self, path, weight=1, **params):
        with self.gate:
            now=time.monotonic()
            due=max(now,self.next_request,self.next_weight if weight else now)
            self.next_request=due+1/12
            if weight: self.next_weight=due+weight/14
        delay=due-time.monotonic()
        if delay>0: time.sleep(delay)
        if time.time()<self.monitor.blocked_until: raise RuntimeError('全市场采集限流退避中')
        if not hasattr(self.local,'client'): self.local.client=PublicClient()
        client=self.local.client
        try: return client.get(path,**params)
        finally:
            self.monitor.blocked_until=max(self.monitor.blocked_until,client.blocked_until)

    def sample(self,symbol):
        if symbol in self.monitor.stock_symbols and not store.settings().get('include_stocks',False): return
        if time.time()<self.retry.get(symbol,0): return
        try:
            m=self.monitor
            # Complete two 4h volume windows and a genuine four-hour baseline.
            seeded_age=time.time()-m.seeded.get(symbol,0)
            stale_stream=time.time()>self.startup_until and time.time()-m.ws_kline_last.get(symbol,0)>25
            if seeded_age>600 or stale_stream and seeded_age>60:
                rows=self.get('/fapi/v1/klines',2,symbol=symbol,interval='1m',limit=store.MINUTE_SEED_LIMIT)
                store.save_minutes(symbol,rows)
                with m.lock:
                    cache=m.spike_bars.setdefault(symbol,{})
                    for r in rows[-125:]:
                        if int(r[6])<time.time()*1000:
                            cache[int(r[0])]=(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[7]),1,time.time())
                m.seeded[symbol]=time.time()
            if time.time()-self.oi_due.get(symbol,0)>=60:
                raw=self.get('/fapi/v1/openInterest',1,symbol=symbol)
                store.save_oi(symbol,[(raw['time'],raw['openInterest'],'live')])
                self.oi_due[symbol]=time.time()
            seed_key=f'oi_seed:{store.RANK_WINDOW_MINUTES}m:{symbol}'
            if time.time()-store.get(seed_key,0)>300:
                history=self.get('/futures/data/openInterestHist',0,symbol=symbol,period='5m',limit=store.OI_SEED_LIMIT)
                store.save_oi(symbol,[(h['timestamp'],h['sumOpenInterest'],'history5m') for h in history])
                store.put(seed_key,time.time())
            self.errors.pop(symbol,None)
        except Exception as exc:
            self.errors[symbol]=str(exc)[:240]
            self.retry[symbol]=time.time()+60

    def run(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            while True:
                started=time.time()
                cfg=store.settings()
                symbols=monitoring.market_symbols(cfg)
                last_report=0
                futures=[pool.submit(self.sample,s) for s in symbols if s not in cfg['watchlist']]
                for future in as_completed(futures):
                    future.result()
                    if time.time()-last_report>=1:
                        self.report(symbols,started)
                        last_report=time.time()
                self.report(symbols,started)
                time.sleep(max(3,min(900,self.monitor.blocked_until-time.time())))

    def report(self,symbols,started):
        store.put('market_health',{'heartbeat':time.time(),'total':len(symbols),
            'history_seeded':sum(s in self.monitor.seeded for s in symbols),
            'round_seconds':round(time.time()-started,1), 'errors':dict(list(self.errors.items())[:12]),
            'scope':'币安USDT加密资产、币股/ETF及大宗永续，默认排除BTC及指数',
            'sampling':'全市场OI约60–180秒，自选OI约10秒；价格/K线实时推送，OI仅排行'})
