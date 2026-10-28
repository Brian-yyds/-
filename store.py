"""Small local adapter: upstream engines own backtests and factor statistics."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USER = ROOT / "user_data"
DB = USER / "workbench.sqlite"
RANK_WINDOW_MINUTES = 240
# Two complete ranking windows plus a small startup/clock margin. Keeping this
# below 500 retains the same Binance request weight as the previous 125 bars.
MINUTE_SEED_LIMIT = 2 * RANK_WINDOW_MINUTES + 19
LIVE_HISTORY_SECONDS = (2 * RANK_WINDOW_MINUTES + 60) * 60
PRICE_HISTORY_SECONDS = (RANK_WINDOW_MINUTES + 20) * 60
OI_SEED_LIMIT = RANK_WINDOW_MINUTES // 5 + 2
DEFAULTS = {
    "watchlist": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
                  "LINKUSDT", "ADAUSDT", "AVAXUSDT", "SUIUSDT"],
    "move15m_pct": 1.5, "volume_ratio": 3.0, "funding_abs_bps": 10.0,
    "cooldown_minutes": 15,
    "window_minutes": RANK_WINDOW_MINUTES, "up_pct": 1.5, "down_pct": 1.5,
    "oi_up_pct": 3.0, "oi_down_pct": 3.0, "quote_ratio": 2.0,
    "min_quote": 0.0, "rule_mode": "任一条件", "priority": [], "overrides": {},
    "spike_up_pct": 10.0, "spike_down_pct": 10.0,
    "quiet_mode": True, "confirm_seconds": 30, "release_seconds": 60,
    "radar_min_quote": 1000000.0, "radar_volume_ratio": 1.8,
    "radar_oi_pct": 2.0, "radar_quiet_pct": 3.0,
    "radar_flow_pct": 15.0, "radar_accel_pct": 1.0,
    "radar_large_quote": 50000.0, "radar_depth_drop_pct": 30.0,
    "radar_max_spread_bps": 20.0, "radar_slip_bps": 20.0,
    "radar_priority_only": False, "depth_band_bps": 10.0,
    "sweep_quote": 10000.0,
    "market_excluded": ["BTCUSDT"],
    "include_stocks": True,
    "top_symbols": ["BTCUSDT", "ETHUSDT", "SOXLUSDT"],
}


@contextmanager
def connect():
    USER.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, time REAL, symbol TEXT, kind TEXT, value REAL, message TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS alert_state (symbol TEXT, kind TEXT, active INTEGER, last_sent REAL, PRIMARY KEY(symbol,kind))")
    con.execute("CREATE TABLE IF NOT EXISTS oi_samples (symbol TEXT, t INTEGER, amount REAL, source TEXT, PRIMARY KEY(symbol,t))")
    con.execute("CREATE TABLE IF NOT EXISTS minute_candles (symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, q REAL, closed INTEGER, seen REAL, PRIMARY KEY(symbol,t))")
    con.execute("CREATE TABLE IF NOT EXISTS spike_alerts (id INTEGER PRIMARY KEY, time REAL, symbol TEXT, direction TEXT, change REAL, base_price REAL, price REAL, base_time INTEGER, event_time INTEGER, basis TEXT, acknowledged INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS spike_state (symbol TEXT, direction TEXT, active INTEGER, event_time INTEGER, PRIMARY KEY(symbol,direction))")
    con.execute("CREATE TABLE IF NOT EXISTS taker_minutes (symbol TEXT,t INTEGER,buy REAL,PRIMARY KEY(symbol,t))")
    con.execute("CREATE TABLE IF NOT EXISTS price_samples (symbol TEXT,bucket INTEGER,t INTEGER,price REAL,PRIMARY KEY(symbol,bucket))")
    con.execute("CREATE TABLE IF NOT EXISTS depth_samples (symbol TEXT,t REAL,data TEXT,PRIMARY KEY(symbol,t))")
    con.execute("CREATE TABLE IF NOT EXISTS radar_events (id INTEGER PRIMARY KEY,time REAL,symbol TEXT,kind TEXT,data TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS radar_state (symbol TEXT,kind TEXT,since REAL,last_seen REAL,last_true REAL,active INTEGER,last_sent REAL,PRIMARY KEY(symbol,kind))")
    con.execute("CREATE TABLE IF NOT EXISTS ai_jobs (id INTEGER PRIMARY KEY,created REAL,updated REAL,profile TEXT,source TEXT,status TEXT,snapshot TEXT,result TEXT,error TEXT,usage TEXT)")
    try:
        with con:
            yield con
    finally:
        con.close()


def get(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key, value):
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, allow_nan=False)))


def settings():
    cfg=DEFAULTS | get("settings", {}) | {"window_minutes": RANK_WINDOW_MINUTES}
    cfg['priority']=list(dict.fromkeys(cfg['priority']+cfg['top_symbols']))
    cfg['watchlist']=list(dict.fromkeys(cfg['top_symbols']+cfg['watchlist']))
    return cfg


def filter_stock_symbols(symbols, cfg=None, info=None):
    """Keep saved selections intact while disabling stocks throughout monitoring."""
    cfg=settings() if cfg is None else cfg
    if cfg.get('include_stocks',True):
        return list(symbols)
    info=get('exchange_info',{}) if info is None else info
    stocks=set(info.get('stock_symbols',[]))
    return [s for s in symbols if s not in stocks]


def record_alert(symbol, kind, value, active, message, cooldown, now=None):
    now = time.time() if now is None else now
    with connect() as con:
        old = con.execute("SELECT active,last_sent FROM alert_state WHERE symbol=? AND kind=?", (symbol, kind)).fetchone()
        last_sent = old[1] if old else 0
        # First observation may alert; repeated conditions obey cooldown.
        send = active and now - last_sent >= cooldown
        if send:
            con.execute("INSERT INTO alerts(time,symbol,kind,value,message) VALUES (?,?,?,?,?)", (now, symbol, kind, value, message))
            last_sent = now
        con.execute("INSERT OR REPLACE INTO alert_state VALUES (?,?,?,?)", (symbol, kind, int(active), last_sent))
        con.execute("DELETE FROM alerts WHERE id NOT IN (SELECT id FROM alerts ORDER BY id DESC LIMIT 5000)")
    return send


def recent_alerts(limit=100):
    with connect() as con:
        rows = con.execute("SELECT time,symbol,kind,value,message FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return rows


def save_oi(symbol, samples):
    with connect() as con:
        con.executemany("INSERT INTO oi_samples VALUES (?,?,?,?) ON CONFLICT(symbol,t) DO UPDATE SET amount=excluded.amount,source=excluded.source WHERE oi_samples.source!='live' OR excluded.source='live'",
                        [(symbol, int(t), float(amount), source) for t, amount, source in samples])


def save_minutes(symbol, rows):
    with connect() as con:
        con.executemany("INSERT OR REPLACE INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)",
            [(symbol,int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[7]),int(r[6] < time.time()*1000),time.time()) for r in rows])


def live_inputs(symbols, now=None, *, oi_window_minutes=None, include_oi=True):
    now = time.time() if now is None else now
    if not symbols:
        return {}, {}
    marks = ",".join("?" for _ in symbols)
    cutoff = int((now - LIVE_HISTORY_SECONDS) * 1000)
    with connect() as con:
        oi=[]
        if include_oi:
            if oi_window_minutes is None:
                oi = con.execute(f"SELECT symbol,t,amount,source FROM oi_samples WHERE symbol IN ({marks}) AND t>=? ORDER BY t", [*symbols,cutoff]).fetchall()
            else:
                # OI rankings need a fresh current sample and candidates around
                # its 4h baseline, not every intermediate sample. Include the
                # largest allowed current age (180s) + historical tolerance (330s).
                current=int(now*1000)
                target=current-oi_window_minutes*60000
                oi=con.execute(f"SELECT symbol,t,amount,source FROM oi_samples WHERE symbol IN ({marks}) AND (t BETWEEN ? AND ? OR t BETWEEN ? AND ?) ORDER BY t",
                               [*symbols,current-180000,current,target-510000,target]).fetchall()
        bars = con.execute(f"SELECT symbol,t,o,h,l,c,q,closed,seen FROM minute_candles WHERE symbol IN ({marks}) AND t>=? ORDER BY t", [*symbols,cutoff]).fetchall()
    oi_map, bar_map = {}, {}
    for symbol,*values in oi:
        oi_map.setdefault(symbol, []).append(values)
    for symbol,*values in bars:
        bar_map.setdefault(symbol, []).append(values)
    return oi_map,bar_map


def prune_live():
    with connect() as con:
        con.execute("DELETE FROM oi_samples WHERE t<?", (int((time.time()-172800)*1000),))
        con.execute("DELETE FROM minute_candles WHERE t<?", (int((time.time()-LIVE_HISTORY_SECONDS)*1000),))
        con.execute("DELETE FROM taker_minutes WHERE t<?", (int((time.time()-10800)*1000),))
        con.execute("DELETE FROM depth_samples WHERE t<?", (time.time()-10800,))
        con.execute("DELETE FROM price_samples WHERE bucket<?", (int((time.time()-PRICE_HISTORY_SECONDS)*1000)//5000,))


def price_baselines(events, window=RANK_WINDOW_MINUTES):
    result={}
    with connect() as con:
        for symbol,event in events.items():
            target=event-window*60000
            r=con.execute('SELECT t,price FROM price_samples WHERE symbol=? AND bucket BETWEEN ? AND ? AND t<=? ORDER BY t DESC LIMIT 1',
                (symbol,(target-10000)//5000,target//5000,target)).fetchone()
            if r and target-r[0]<=10000: result[symbol]=r
    return result


def spike_states():
    with connect() as con:
        return {(s,d):bool(active) for s,d,active in con.execute("SELECT symbol,direction,active FROM spike_state")}


def set_spike_episode(symbol,direction,active,event_time,change=None,reference=None,price=None):
    """Persist one alarm per episode; restart and acknowledgement do not re-arm it."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        previous=con.execute("SELECT active,event_time FROM spike_state WHERE symbol=? AND direction=?",(symbol,direction)).fetchone()
        if previous and event_time<previous[1]:
            return False
        created=bool(active and not (previous and previous[0]))
        if created:
            con.execute("INSERT INTO spike_alerts(time,symbol,direction,change,base_price,price,base_time,event_time,basis) VALUES (?,?,?,?,?,?,?,?,?)",
                        (event_time/1000,symbol,direction,change,reference['price'],price,reference['time'],event_time,reference['basis']))
        con.execute("INSERT OR REPLACE INTO spike_state VALUES (?,?,?,?)",(symbol,direction,int(active),event_time))
        # Keep every unacknowledged alarm; prune acknowledged history only.
        con.execute("DELETE FROM spike_alerts WHERE acknowledged=1 AND id NOT IN (SELECT id FROM spike_alerts ORDER BY id DESC LIMIT 5000)")
    return created


def spike_alerts(unacknowledged=False,limit=100):
    with connect() as con:
        where="WHERE acknowledged=0" if unacknowledged else ""
        return con.execute(f"SELECT id,time,symbol,direction,change,base_price,price,base_time,event_time,basis,acknowledged FROM spike_alerts {where} ORDER BY id DESC LIMIT ?",(limit,)).fetchall()


def acknowledge_spikes(ids):
    with connect() as con:
        con.executemany("UPDATE spike_alerts SET acknowledged=1 WHERE id=?",[(int(i),) for i in ids])
