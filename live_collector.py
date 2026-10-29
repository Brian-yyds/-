"""Near-real-time monitoring: public price/kline streams + sampled OI."""
import json
import threading
import time
import math
from collections import defaultdict,deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from websockets.sync.client import connect
import store
import monitoring
from collector import PublicClient
from market_scanner import crypto_contracts,stock_contracts,commodity_contracts,MarketScanner


class Monitor:
    def __init__(self):
        self.client = PublicClient()
        self.local = threading.local()
        self.lock = threading.Lock()
        self.valid = set()
        self.market_valid = set()
        self.stock_symbols = set(store.get('exchange_info',{}).get('stock_symbols',[]))
        self.ticks = {}
        self.seeded = {}
        self.blocked_until = 0
        self.ws_last = 0
        self.ws_kline_last = {}
        self.ws_error = "正在连接"
        self.last_quotes = 0
        self.spike_prices=defaultdict(deque)
        self.spike_bars={}
        self.spike_active=store.spike_states()
        self.spike_config = store.settings()
        self.spike_config_time = time.time()

    def inspect_spike(self,symbol,price,event_time):
        # Called by the price/kline stream, independently of the 10s OI loop.
        if not math.isfinite(price) or price<=0 or not 0<=time.time()*1000-event_time<=20000:
            return
        with self.lock:
            if time.time()-self.spike_config_time>=3:
                self.spike_config=store.settings()
                self.spike_config_time=time.time()
            if not self.spike_config.get('include_stocks',False) and symbol in self.stock_symbols:
                return
            observations=self.spike_prices[symbol]
            if observations and event_time<=observations[-1][0]:
                return
            while observations and observations[0][0]<event_time-monitoring.SPIKE_WINDOW_MS:
                observations.popleft()
            metrics=monitoring.spike_metrics(observations,self.spike_bars.get(symbol,{}).values(),price,event_time)
            observations.append((event_time,price))
            if metrics is None:
                return
            for direction,change,reference in [("急涨",metrics['up_pct'],metrics['up_reference']),
                                                ("急跌",-metrics['down_pct'],metrics['down_reference'])]:
                active=self.spike_active.get((symbol,direction),False)
                threshold=float(self.spike_config['spike_up_pct' if direction=='急涨' else 'spike_down_pct'])
                if not active and change>=threshold-1e-9:
                    signed_change=change if direction=="急涨" else -change
                    reference=reference|{'basis':reference['basis']+f' · 阈值{threshold:g}%'}
                    created=store.set_spike_episode(symbol,direction,True,event_time,signed_change,reference,price)
                    self.spike_active[(symbol,direction)]=True
                    if created:
                        print(json.dumps({"alarm":"15m_spike","symbol":symbol,"direction":direction,"threshold":threshold,"change":signed_change},ensure_ascii=False),flush=True)
                elif active and change<threshold*.95:
                    store.set_spike_episode(symbol,direction,False,event_time)
                    self.spike_active[(symbol,direction)]=False

    def universe(self):
        info = self.client.get("/fapi/v1/exchangeInfo")
        self.valid = {s["symbol"] for s in info["symbols"] if s["contractType"]=="PERPETUAL"
                      and s["quoteAsset"]=="USDT" and s["marginAsset"]=="USDT" and s["status"]=="TRADING"}
        crypto = set(crypto_contracts(info))
        stocks = set(stock_contracts(info))
        commodities = set(commodity_contracts(info))
        self.stock_symbols = stocks
        self.valid |= stocks | commodities
        self.market_valid = crypto | stocks | commodities
        store.put("exchange_info", {"fetched_at":time.time(),"symbols":sorted(self.valid),
            'alt_symbols':sorted(crypto),'stock_symbols':sorted(stocks),'commodity_symbols':sorted(commodities),'market_symbols':sorted(self.market_valid)})

    def quotes(self):
        raw = self.client.get("/fapi/v1/ticker/24hr")
        with self.lock:
            for t in raw:
                if t["symbol"] in self.valid:
                    old = self.ticks.get(t["symbol"],{})
                    if int(t["closeTime"]) >= old.get("event_time",0):
                        self.ticks[t["symbol"]] = old | {"price":float(t["lastPrice"]),"change24h":float(t["priceChangePercent"]),
                            "quote24h":float(t["quoteVolume"]),"event_time":int(t["closeTime"])}
            store.put("live_market", {"tickers":self.ticks,"saved_at":time.time()})
        self.last_quotes = time.time()

    def stream(self):
        while True:
            cfg=store.settings()
            symbols = tuple(s for s in store.filter_stock_symbols(cfg['watchlist'],cfg)[:50] if s in self.valid)
            market=tuple(sorted(store.filter_stock_symbols(self.valid,cfg)))
            streams = ["!ticker@arr", *[s.lower()+'@kline_1m' for s in market]]
            url = "wss://fstream.binance.com/market/stream?streams="+"/".join(streams)
            pending = {}
            price_pending = {}
            last_flush, last_check = 0, 0
            try:
                with connect(url, open_timeout=12, ping_interval=20, close_timeout=3,max_size=4*1024*1024) as ws:
                    connected_at=time.time()
                    self.ws_error = ""
                    while True:
                        now = time.time()
                        if now-last_check>3:
                            current_cfg=store.settings()
                            current = tuple(s for s in store.filter_stock_symbols(current_cfg['watchlist'],current_cfg)[:50] if s in self.valid)
                            if current != symbols or tuple(sorted(store.filter_stock_symbols(self.valid,current_cfg)))!=market: break
                            last_check = now
                        try:
                            packet = json.loads(ws.recv(timeout=1))
                            data = packet.get("data",packet)
                            self.ws_last = time.time()
                            if isinstance(data,list):
                                spike_ticks=[]
                                with self.lock:
                                    for t in data:
                                        symbol = t.get("s")
                                        if symbol in self.valid and t.get("st",1)==1:
                                            self.ticks[symbol] = {"price":float(t["c"]),"change24h":float(t["P"]),
                                                "quote24h":float(t["q"]),"event_time":int(t["E"])}
                                            stamp=int(t['E'])
                                            price_pending.setdefault((symbol,stamp//5000),(symbol,stamp//5000,stamp,float(t['c'])))
                                            if symbol in self.market_valid and symbol not in self.spike_config.get('market_excluded',['BTCUSDT']):
                                                spike_ticks.append((symbol,float(t['c']),int(t['E'])))
                                for spike_tick in spike_ticks:
                                    self.inspect_spike(*spike_tick)
                            elif data.get("e")=="kline" and data.get("s") in self.valid and data.get('st',1)==1:
                                k = data["k"]
                                pending[(data["s"],k["t"])] = (data["s"],int(k["t"]),float(k["o"]),float(k["h"]),
                                    float(k["l"]),float(k["c"]),float(k["q"]),int(k["x"]),time.time())
                                with self.lock:
                                    cache=self.spike_bars.setdefault(data['s'],{})
                                    cache[int(k['t'])]=pending[(data['s'],k['t'])][1:]
                                    self.spike_bars[data['s']]={t:r for t,r in cache.items() if t>=int(k['t'])-7200000}
                                if data['s'] in self.market_valid and data['s'] not in self.spike_config.get('market_excluded',['BTCUSDT']):
                                    self.inspect_spike(data['s'],float(k['c']),int(data['E']))
                                self.ws_kline_last[data["s"]] = time.time()
                        except TimeoutError:
                            pass
                        if now-last_flush>=2:
                            with store.connect() as con:
                                con.executemany("INSERT OR REPLACE INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)",list(pending.values()))
                                con.executemany('INSERT OR IGNORE INTO price_samples VALUES (?,?,?,?)',list(price_pending.values()))
                            pending.clear()
                            price_pending.clear()
                            with self.lock:
                                store.put("live_market",{"tickers":self.ticks,"saved_at":time.time()})
                            store.put("stream_health",{"connected":True,"last_message":self.ws_last,"error":""})
                            last_flush=now
                        if now-max(self.ws_last,connected_at)>25:
                            raise RuntimeError("行情推送25秒无数据，正在重连")
            except Exception as exc:
                self.ws_error = str(exc)
                store.put("stream_health",{"connected":False,"last_message":self.ws_last,"error":str(exc)})
                time.sleep(5)

    def sample(self,symbol):
        if time.time()<self.blocked_until:
            raise RuntimeError("接口限流退避中")
        if not hasattr(self.local,"client"):
            self.local.client=PublicClient()
        client=self.local.client
        warnings=[]
        try:
            # Seed both rolling four-hour volume windows and the price baseline.
            if time.time()-self.seeded.get(symbol,0)>120 or time.time()-self.ws_kline_last.get(symbol,0)>15:
                minute_rows=client.get("/fapi/v1/klines",symbol=symbol,interval="1m",limit=store.MINUTE_SEED_LIMIT)
                store.save_minutes(symbol,minute_rows)
                with self.lock:
                    self.spike_bars[symbol]={int(r[0]):(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[7]),int(r[6]<time.time()*1000),time.time()) for r in minute_rows[-125:]}
                self.seeded[symbol]=time.time()
            raw=client.get("/fapi/v1/openInterest",symbol=symbol)
            store.save_oi(symbol,[(raw["time"],raw["openInterest"],"live")])
            seed_key=f'oi_seed:{store.RANK_WINDOW_MINUTES}m:{symbol}'
            if time.time()-store.get(seed_key,0)>300:
                try:
                    history=client.get("/futures/data/openInterestHist",symbol=symbol,period="5m",limit=store.OI_SEED_LIMIT)
                    store.save_oi(symbol,[(h["timestamp"],h["sumOpenInterest"],"history5m") for h in history])
                    store.put(seed_key,time.time())
                except Exception as exc:
                    warnings.append("OI历史基准："+str(exc))
            return warnings
        finally:
            if client.blocked_until>time.time():
                self.blocked_until=max(self.blocked_until,client.blocked_until)

    def run(self):
        self.universe()
        self.quotes()
        threading.Thread(target=self.stream,daemon=True).start()
        scanner=MarketScanner(self)
        threading.Thread(target=scanner.run,daemon=True).start()
        from price_history import Monitor as ReturnHistory
        threading.Thread(target=ReturnHistory(scanner.get).run,daemon=True).start()
        last_universe,last_prune=time.time(),0
        with ThreadPoolExecutor(max_workers=4) as pool:
            while True:
                started=time.time()
                errors=[]
                cfg=store.settings()
                try:
                    if time.time()-last_universe>3600:
                        self.universe()
                        last_universe=time.time()
                    if time.time()-self.last_quotes>30 or time.time()-self.ws_last>15:
                        self.quotes()
                    futures={pool.submit(self.sample,s):s for s in store.filter_stock_symbols(cfg['watchlist'],cfg)[:50] if s in self.valid}
                    for future in as_completed(futures):
                        s=futures[future]
                        try:
                            errors += [s+": "+e for e in future.result()]
                        except Exception as exc:
                            errors.append(s+": "+str(exc))
                    cfg=store.settings()
                    monitored=set(monitoring.market_symbols(cfg))
                    # If the stream is unavailable, evaluate with fresh REST
                    # quotes as a fallback. This may be delayed by the OI cycle.
                    with self.lock:
                        spike_ticks=[(s,t['price'],t['event_time']) for s,t in self.ticks.items() if s in monitored]
                    for spike_tick in spike_ticks:
                        self.inspect_spike(*spike_tick)
                    # Only <=15m price-spike episodes can create alerts.
                    # OI feeds the ranking; no OI, volume or composite alarms.
                    if time.time()-last_prune>3600:
                        store.prune_live()
                        last_prune=time.time()
                except Exception as exc:
                    errors.append(str(exc))
                    self.blocked_until=max(self.blocked_until,self.client.blocked_until)
                store.put("monitor_health",{"heartbeat":time.time(),"errors":errors,"symbols":len(store.filter_stock_symbols(cfg['watchlist'],cfg)),'market_symbols':len(monitoring.market_symbols(cfg)),
                    "cycle_seconds":time.time()-started,"blocked_until":self.blocked_until,'mode':'15m_price_alerts_only'})
                print(json.dumps({"watching":len(cfg["watchlist"]),"errors":errors,"seconds":round(time.time()-started,2)},ensure_ascii=False),flush=True)
                time.sleep(max(1,10-(time.time()-started),min(900,self.blocked_until-time.time())))


if __name__=="__main__":
    while True:
        try:
            Monitor().run()
        except Exception as exc:
            store.put("monitor_health",{"heartbeat":time.time(),"errors":[str(exc)]})
            print(str(exc),flush=True)
            time.sleep(15)
