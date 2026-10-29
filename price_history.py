"""Exact minute-open baselines for rolling 72h returns, cached ahead in batches."""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import store

MINUTE=60000
WINDOW=72*60*MINUTE
BATCH=90  # Historical minutes already exist; one request covers the next ~89m.


def schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS return_baselines (symbol TEXT,t INTEGER,price REAL,observed REAL,PRIMARY KEY(symbol,t))')


def baselines(events):
    if not events:return {}
    targets={s:(int(t)-WINDOW)//MINUTE*MINUTE for s,t in events.items() if t}
    if not targets:return {}
    with store.connect() as con:
        schema(con)
        rows=con.execute('SELECT symbol,t,price FROM return_baselines WHERE t BETWEEN ? AND ?',
                         (min(targets.values()),max(targets.values()))).fetchall()
    return {s:(t,p) for s,t,p in rows if targets.get(s)==t and math.isfinite(p) and p>0}


class Monitor:
    def __init__(self,get):
        self.get=get  # Share the all-market scanner's existing request/weight gate.

    def sample(self,symbol,now=None):
        now=time.time() if now is None else now
        target=(int(now*1000)-WINDOW)//MINUTE*MINUTE
        with store.connect() as con:
            schema(con)
            hit=con.execute('SELECT 1 FROM return_baselines WHERE symbol=? AND t=?',(symbol,target)).fetchone()
        if hit or now<store.get('return_retry:'+symbol,0):return
        try:
            end=target+(BATCH-1)*MINUTE+MINUTE-1
            raw=self.get('/fapi/v1/klines',1,symbol=symbol,interval='1m',startTime=target,endTime=end,limit=BATCH)
            if not isinstance(raw,list):raise ValueError('invalid history')
            parsed={}
            for row in raw:
                t=int(row[0]);p=float(row[1])
                if t%MINUTE or int(row[6])!=t+MINUTE-1 or not math.isfinite(p) or p<=0:
                    raise ValueError('invalid baseline')
                if target<=t<=end:parsed[t]=p
            with store.connect() as con:
                schema(con)
                con.executemany('INSERT OR REPLACE INTO return_baselines VALUES (?,?,?,?)',
                                [(symbol,t,p,now) for t,p in parsed.items()])
            # New listings and closed stock sessions have no exact 72h baseline.
            store.put('return_retry:'+symbol,0 if target in parsed else now+600)
        except Exception:
            store.put('return_retry:'+symbol,now+60)

    def run(self):
        import monitoring
        with ThreadPoolExecutor(max_workers=2) as pool:
            while True:
                started=time.time()
                try:
                    cfg=store.settings()
                    symbols=list(dict.fromkeys(cfg['top_symbols']+monitoring.market_symbols(cfg)))
                    # Visible leaders first; all remaining symbols follow in the same round.
                    rows=monitoring.build_rows(cfg,scope='market')
                    leaders=list(dict.fromkeys(r['symbol'] for k in ('up','down','oi_up','oi_down') for r in monitoring.ranked(rows,k)[:30]))
                    ordered=list(dict.fromkeys(leaders+symbols))
                    for future in as_completed([pool.submit(self.sample,s) for s in ordered]):future.result()
                    with store.connect() as con:
                        schema(con)
                        con.execute('DELETE FROM return_baselines WHERE t<?',(int(started*1000)-WINDOW-5*MINUTE,))
                    store.put('return_history_health',dict(heartbeat=time.time(),symbols=len(ordered),window_hours=72))
                except Exception:
                    store.put('return_history_health',dict(heartbeat=time.time(),status='历史基准补齐中',window_hours=72))
                time.sleep(30)
