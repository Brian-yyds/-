"""Five-minute current valuations, separate from durable alarm events."""
import json
import time
import store

INTERVAL=300
STALE_AFTER=600


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS gmgn_market_caps (
        chain TEXT,address TEXT,market_cap REAL,observed REAL DEFAULT 0,
        attempted REAL DEFAULT 0,retry_at REAL DEFAULT 0,PRIMARY KEY(chain,address))''')


def observe(con,token,stamp):
    """Piggyback successful scans/details, including observations below alarm thresholds."""
    import gmgn_monitor as g
    from gmgn_tracking import identity
    cap=g.number(token.get('market_cap'));stamp=g.number(stamp)
    if cap is None or stamp is None or not g.valid_address(token.get('chain'),token.get('address')):return False
    if g.equities.equity_reason(token):return False
    chain,address=identity(token['chain'],token['address'])
    old=con.execute('SELECT observed FROM gmgn_market_caps WHERE chain=? AND address=?',(chain,address)).fetchone()
    if old and old[0] and stamp-old[0]<INTERVAL:return False
    con.execute('''INSERT INTO gmgn_market_caps(chain,address,market_cap,observed) VALUES (?,?,?,?)
        ON CONFLICT(chain,address) DO UPDATE SET market_cap=excluded.market_cap,observed=excluded.observed,retry_at=0''',
        (chain,address,cap,stamp))
    return True


def current(con,token,now):
    from gmgn_tracking import identity
    key=identity(token['chain'],token['address'])
    row=con.execute('SELECT market_cap,observed FROM gmgn_market_caps WHERE chain=? AND address=?',key).fetchone()
    if not row or row[0] is None or not 0<row[1]<=now:
        return dict(current_market_cap=None,market_cap_observed=None,market_cap_stale=True)
    return dict(current_market_cap=row[0],market_cap_observed=row[1],market_cap_stale=now-row[1]>STALE_AFTER)


def refresh(client,cfg,now=None):
    """Only the three visible alarms need a fallback query when normal data no longer covers them."""
    import gmgn_monitor as g
    from gmgn_tracking import identity,parse_info
    now=time.time() if now is None else now
    if not cfg.get('enabled',True) or now<store.get('gmgn_rate_limit_until',0):return dict(attempted=0,succeeded=0)
    visible={identity(r['token']['chain'],r['token']['address']) for r in g.recent_alerts(3)
             if g.valid_address(r['token'].get('chain'),r['token'].get('address'))}
    due=[]
    with store.connect() as con:
        schema(con)
        for key in sorted(visible):
            con.execute('INSERT OR IGNORE INTO gmgn_market_caps(chain,address) VALUES (?,?)',key)
            observed,retry=con.execute('SELECT observed,retry_at FROM gmgn_market_caps WHERE chain=? AND address=?',key).fetchone()
            if now-observed>=INTERVAL and now>=retry:due.append(key)
        # Reserve the interval before I/O so failure/restart cannot trigger a tight retry loop.
        con.executemany('UPDATE gmgn_market_caps SET attempted=?,retry_at=? WHERE chain=? AND address=?',
                        [(now,now+INTERVAL,*key) for key in due])
    report=dict(attempted=len(due),succeeded=0)
    if not due:return report
    try:
        result=client.query(['token','info-batch','--params',json.dumps([dict(chain=c,address=a) for c,a in due])])
        if not isinstance(result,list):raise g.QueryError('GMGN 市值数据格式变化，保留上次市值')
        seen=set()
        for item in result:
            if not isinstance(item,dict) or not item.get('ok'):continue
            key=identity(str(item.get('chain','')),str(item.get('address','')))
            if key not in due or key in seen:continue
            seen.add(key);observed=g.number(item.get('observed'))
            if observed is None or not now-1<=observed<=time.time()+2:continue
            try:token,_=parse_info(item.get('data'),*key,observed)
            except (TypeError,ValueError):continue
            with store.connect() as con:
                if observe(con,token,observed):report['succeeded']+=1
    finally:
        store.put('gmgn_cap_health',report|dict(updated=time.time(),interval_seconds=INTERVAL,
                                             failed=report['attempted']-report['succeeded']))
    return report
