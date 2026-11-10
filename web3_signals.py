"""Public Binance Wallet signals and fresh GMGN intersections. Read-only."""
from contextlib import contextmanager
from datetime import datetime
import json
import math
import os
import time
from urllib.parse import urlsplit
import requests
import store

ENDPOINT='https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai'
CHAINS={'sol':'CT_501','bsc':'56'}
POLL_SECONDS=60
PUBLISH_SECONDS=60
DISPLAY_LIMIT=100
WEB3_FRESH=150
GMGN_FRESH=180
ACTIVE={'active','valid'}
ALARM_POOL_SECONDS=10*86400
MAX_PAGES_PER_CYCLE=5
POOL_VERSION='2026-11-09-event-pool-v3'


def configuration():
    return {'enabled':True}|store.get('web3_signal_config',{})


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS wallet_signals (
        chain TEXT,signal_id TEXT,address TEXT,triggered REAL,observed REAL,
        status TEXT,data TEXT,PRIMARY KEY(chain,signal_id))''')
    con.execute('''CREATE TABLE IF NOT EXISTS gmgn_signal_candidates (
        chain TEXT,address TEXT,observed REAL,data TEXT,PRIMARY KEY(chain,address))''')
    con.execute('''CREATE TABLE IF NOT EXISTS special_alerts (
        id INTEGER PRIMARY KEY,chain TEXT,address TEXT,day TEXT,triggered REAL,
        data TEXT,UNIQUE(chain,address,day))''')
    con.execute('''CREATE TABLE IF NOT EXISTS gmgn_alarm_evidence (
        alert_id INTEGER PRIMARY KEY,chain TEXT,address TEXT,triggered REAL,rule TEXT,data TEXT)''')
    con.execute('CREATE INDEX IF NOT EXISTS gmgn_evidence_token ON gmgn_alarm_evidence(chain,address,alert_id)')
    con.execute('''CREATE TABLE IF NOT EXISTS wallet_signal_events (
        chain TEXT,signal_id TEXT,address TEXT,triggered REAL,first_observed REAL,
        last_observed REAL,data TEXT,PRIMARY KEY(chain,signal_id))''')
    con.execute('CREATE INDEX IF NOT EXISTS wallet_events_token ON wallet_signal_events(chain,address,last_observed)')
    con.execute('''CREATE TABLE IF NOT EXISTS special_signal_usage (
        chain TEXT,signal_id TEXT,address TEXT,handled_at REAL,disposition TEXT,special_id INTEGER,
        PRIMARY KEY(chain,signal_id))''')


def number(value):
    if isinstance(value,bool):return None
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else None
    except (TypeError,ValueError):return None


def normalize(raw,chain,observed):
    import gmgn_monitor as g
    import gmgn_tracking as t
    if not isinstance(raw,dict) or raw.get('chainId')!=CHAINS.get(chain):return None
    address=raw.get('contractAddress')
    if not g.valid_address(chain,address):return None
    ident=raw.get('signalId');triggered=number(raw.get('signalTriggerTime'))
    if isinstance(ident,bool) or not str(ident).isdigit() or triggered is None:return None
    triggered/=1000
    if not 0<triggered<=observed+5:return None
    direction=str(raw.get('direction','')).lower()
    if direction not in ('buy','sell'):return None
    logo=str(raw.get('logoUrl') or '')
    if logo.startswith('/') and not logo.startswith('//'):logo='https://bin.bnbstatic.com'+logo
    try:
        p=urlsplit(logo)
        if p.scheme!='https' or p.hostname!='bin.bnbstatic.com' or p.username or p.password:logo=''
    except ValueError:logo=''
    return dict(chain=chain,address=t.identity(chain,address)[1],signal_id=str(ident),
        symbol=str(raw.get('ticker') or '')[:100],logo=logo,triggered=triggered,observed=observed,
        direction=direction,status=str(raw.get('status') or 'unknown')[:40],
        smart_money_count=number(raw.get('smartMoneyCount')),
        current_price=number(raw.get('currentPrice')),market_cap=number(raw.get('currentMarketCap')),
        alert_price=number(raw.get('alertPrice')),alert_market_cap=number(raw.get('alertMarketCap')),
        exit_rate=number(raw.get('exitRate')),signal_type=str(raw.get('smartSignalType') or '')[:60])


class SignalError(Exception):
    def __init__(self,message,wait=30):
        super().__init__(message);self.wait=max(POLL_SECONDS,min(3600,wait))


class Client:
    def __init__(self):self.session=requests.Session()
    def page(self,chain,page=1):
        try:
            with self.session.post(ENDPOINT,json=dict(chainId=CHAINS[chain],page=page,pageSize=100),
                    headers={'Accept-Encoding':'identity','User-Agent':'binance-web3/2.0 (Skill)'},
                    timeout=(5,12),allow_redirects=False,stream=True) as response:
                if response.status_code==429:
                    wait=response.headers.get('Retry-After','')
                    raise SignalError('币安钱包信号限频，等待恢复',int(wait) if wait.isdigit() else 60)
                if response.status_code!=200:raise SignalError('币安钱包信号暂不可用')
                parts=[];size=0
                for part in response.iter_content(65536):
                    size+=len(part)
                    if size>2*1024*1024:raise SignalError('币安钱包信号内容过大，保留缓存')
                    parts.append(part)
                payload=json.loads(b''.join(parts))
        except (requests.RequestException,ValueError):
            raise SignalError('币安钱包信号连接异常，保留缓存') from None
        if not isinstance(payload,dict) or payload.get('code')!='000000' or payload.get('success') is not True:
            raise SignalError('币安钱包信号返回异常，保留缓存')
        raw=payload.get('data');stamp=time.time()
        if not isinstance(raw,list) or len(raw)>100:raise SignalError('币安钱包信号字段变化')
        rows=[normalize(r,chain,stamp) for r in raw]
        if any(r is None for r in rows):raise SignalError('币安钱包信号字段不完整')
        return rows


def record_gmgn_scan(chain,tokens,stamp,con=None,cfg=None):
    """Record and match in the SAME transaction as the originating GMGN alarm."""
    import gmgn_monitor as g
    import gmgn_tracking as t
    def save(con):
        schema(con)
        for raw in tokens:
            if not isinstance(raw,dict) or raw.get('chain')!=chain or not g.valid_address(chain,raw.get('address')):continue
            address=t.identity(chain,raw['address'])[1]
            con.execute('''INSERT INTO gmgn_signal_candidates VALUES (?,?,?,?)
                ON CONFLICT(chain,address) DO UPDATE SET observed=excluded.observed,data=excluded.data
                WHERE excluded.observed>=observed''',(chain,address,stamp,json.dumps(raw,ensure_ascii=False,allow_nan=False)))
        con.execute('DELETE FROM gmgn_signal_candidates WHERE observed<?',(stamp-86400,))
        reconcile(con,max(stamp,time.time()),cfg)
    if con is not None:save(con)
    else:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');g.schema(db);save(db)


def capture_wallet_events(con,rows,now):
    """Latch actually observed valid events; later completed/expired states cannot erase them."""
    import gmgn_monitor as g
    import gmgn_tracking as t
    for row in rows:
        if (row.get('status') not in ACTIVE or row.get('chain') not in CHAINS
            or not g.valid_address(row.get('chain'),row.get('address'))):continue
        observed=number(row.get('observed'));triggered=number(row.get('triggered'))
        if observed is None or triggered is None or not 0<triggered<=observed+5 or not 0<=now-observed<=ALARM_POOL_SECONDS:continue
        chain,address=t.identity(row['chain'],row['address'])
        con.execute('''INSERT INTO wallet_signal_events VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(chain,signal_id) DO UPDATE SET
            last_observed=excluded.last_observed,data=excluded.data
            WHERE excluded.last_observed>last_observed AND address=excluded.address''',
            (chain,row['signal_id'],address,triggered,observed,observed,json.dumps(row,ensure_ascii=False,allow_nan=False)))


def pool_configuration(con):
    import gmgn_monitor as g
    return (g.DEFAULTS|store_value(con,'gmgn_config',{})|
            dict(exclude_equities=True,tracking_days=10,daily_limit=1))


def reconcile(con,now,cfg=None):
    """Two durable event datasets. No fresh-volume recheck after a real GMGN alarm."""
    import gmgn_monitor as g
    import gmgn_tracking as t
    cfg=pool_configuration(con) if cfg is None else cfg
    # Preserve proof of the actual alarm before mutable latest-volume updates.
    con.execute('''INSERT OR IGNORE INTO gmgn_alarm_evidence
        SELECT a.id,a.chain,a.address,a.triggered,a.rule,a.data FROM gmgn_alerts a
        JOIN gmgn_watch w ON w.chain=a.chain AND w.address=a.address
        WHERE w.first_alert<=? AND w.expires_at>? AND a.triggered<=?''',(now,now,now))
    # Upgrade existing persisted valid observations, without inventing past states.
    saved=[json.loads(row[0]) for row in con.execute(
        'SELECT data FROM wallet_signals WHERE status IN (?,?) AND observed>=? AND observed<=?',
        ('active','valid',now-ALARM_POOL_SECONDS,now))]
    capture_wallet_events(con,saved,now)
    seed=store_value(con,'special_usage_seed_id',0)
    legacy=con.execute('SELECT id,chain,address,triggered,data FROM special_alerts WHERE id>? ORDER BY id',(seed,)).fetchall()
    for event_id,chain,address,triggered,raw in legacy:
        event=json.loads(raw)
        con.execute('INSERT OR IGNORE INTO special_signal_usage VALUES (?,?,?,?,?,?)',
            (chain,event['signal']['signal_id'],address,triggered,'emitted',event_id))
    if legacy:con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('special_usage_seed_id',json.dumps(legacy[-1][0])))
    if not pool_configuration(con)['enabled'] or not store_value(con,'web3_signal_config',{}).get('enabled',True):return 0
    pool=con.execute('''SELECT w.chain,w.address,w.first_alert,w.expires_at,
        e.alert_id,e.triggered,e.rule,e.data FROM gmgn_watch w
        JOIN gmgn_alarm_evidence e ON e.alert_id=(SELECT MAX(e2.alert_id) FROM gmgn_alarm_evidence e2
            WHERE e2.chain=w.chain AND e2.address=w.address)
        WHERE w.first_alert<=? AND w.expires_at>?''',(now,now)).fetchall()
    inserted=0
    for chain,address,first,expires,ident,triggered,rule,raw in pool:
        token=json.loads(raw)
        if chain not in CHAINS or not g.valid_address(chain,address) or g.equities.equity_reason(token):continue
        rows=con.execute('''SELECT first_observed,last_observed,data FROM wallet_signal_events e
            WHERE chain=? AND address=? AND last_observed>=? AND first_observed<=?
                AND NOT EXISTS (SELECT 1 FROM special_signal_usage u WHERE u.chain=e.chain AND u.signal_id=e.signal_id)
            ORDER BY triggered DESC,last_observed DESC,signal_id DESC''',
            (chain,address,now-ALARM_POOL_SECONDS,now)).fetchall()
        if not rows:continue
        web_first,web_last,web_raw=rows[0];signal=json.loads(web_raw)
        value=dict(token=token,rule=rule,gmgn_observed=triggered,gmgn_alert_id=ident,
            gmgn_triggered=triggered,signal=signal,web3_first_observed=web_first,web3_last_observed=web_last,
            watch_first_alert=first,watch_expires_at=expires,match_mode='alarm_pool_10d')
        result=con.execute('INSERT OR IGNORE INTO special_alerts(chain,address,day,triggered,data) VALUES (?,?,?,?,?)',
            (chain,address,t.day(now),now,json.dumps(value,ensure_ascii=False,allow_nan=False)))
        inserted+=result.rowcount
        event_id=result.lastrowid if result.rowcount else con.execute(
            'SELECT id FROM special_alerts WHERE chain=? AND address=? AND day=?',(chain,address,t.day(now))).fetchone()[0]
        # Consume every qualified event for this token TODAY. Otherwise a suppressed
        # signal could drip out tomorrow, or the newest used signal could hide a late one.
        con.executemany('INSERT OR IGNORE INTO special_signal_usage VALUES (?,?,?,?,?,?)',
            [(chain,json.loads(r[2])['signal_id'],address,now,'emitted' if result.rowcount and i==0 else 'daily_suppressed',event_id)
             for i,r in enumerate(rows)])
    if inserted:
        cached=store_value(con,'signal_dashboard',{})
        cached.update(special_alerts=special_rows(con),special_updated_at=now,match_mode='alarm_pool_10d')
        con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('signal_dashboard',json.dumps(cached,ensure_ascii=False,allow_nan=False)))
    return inserted


def special_rows(con):
    return [dict(id=i,triggered=stamp,**json.loads(data)) for i,stamp,data in
            con.execute('SELECT id,triggered,data FROM special_alerts ORDER BY triggered DESC,id DESC LIMIT ?',(DISPLAY_LIMIT,))]


def record_wallet_batch(chain,rows,stamp,chain_health):
    import signal_ingest
    return signal_ingest.submit('wallet',dict(chain=chain,rows=rows,stamp=stamp,health=chain_health))


def _record_wallet_batch(chain,rows,stamp,chain_health):
    """Commit accepted Web3 events and their special matches atomically, per successful chain."""
    import gmgn_monitor as g
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');schema(con);g.schema(con)
        for row in rows:
            if row['chain']!=chain:raise ValueError('Wallet batch chain mismatch')
            con.execute('''INSERT INTO wallet_signals VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chain,signal_id) DO UPDATE SET observed=excluded.observed,status=excluded.status,data=excluded.data
                WHERE excluded.observed>=observed''',
                (chain,row['signal_id'],row['address'],row['triggered'],row['observed'],row['status'],json.dumps(row,ensure_ascii=False,allow_nan=False)))
        capture_wallet_events(con,rows,stamp)
        health=store_value(con,'web3_signal_health',{});health.setdefault('chains',{})[chain]=chain_health
        health.update(heartbeat=stamp,enabled=True)
        con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('web3_signal_health',json.dumps(health,ensure_ascii=False,allow_nan=False)))
        reconcile(con,max(stamp,time.time()))


def signal_is_current(row,health,now):
    state=health.get('chains',{}).get(row['chain'],{})
    return (row['status'] in ACTIVE and not state.get('error')
            and 0<=now-state.get('updated',0)<=WEB3_FRESH
            and 0<=now-row['observed']<=WEB3_FRESH and row['triggered']<=now)


def publish(now=None):
    """One durable view for both feed boxes and the intersection box."""
    import gmgn_monitor as g
    import gmgn_tracking as t
    now=time.time() if now is None else now
    health=store.get('web3_signal_health',{});cfg=g.configuration();web_enabled=bool(configuration()['enabled'])
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');schema(con);g.schema(con)
        reconcile(con,now,cfg)
        rows=[json.loads(r[0]) for r in con.execute('SELECT data FROM wallet_signals ORDER BY triggered DESC')]
        current={}
        if web_enabled:
            for r in rows:
                if signal_is_current(r,health,now):current.setdefault(t.identity(r['chain'],r['address']),r)
        candidates=[]
        if cfg['enabled']:
            for chain,address,stamp,data in con.execute('SELECT chain,address,observed,data FROM gmgn_signal_candidates WHERE observed<=? AND observed>=?',(now,now-GMGN_FRESH)):
                tok=json.loads(data)
                watch=con.execute('SELECT initial_rule,expires_at FROM gmgn_watch WHERE chain=? AND address=?',(chain,address)).fetchone()
                followup=bool(watch and watch[1]>now and watch[0] in ('今日毕业放量','毕业代币再次放量'))
                rule=g.reason(tok,cfg,now,followup=followup)
                if not rule:continue
                candidates.append(dict(token=tok,observed=stamp,rule=rule))
        special=special_rows(con)
        alarms=g.alarm_history(con,limit=DISPLAY_LIMIT,now=now,include_market_cap=True)
        display_rows=sorted(rows,key=lambda r:(not(web_enabled and signal_is_current(r,health,now)),-r['triggered']))
        value=dict(generated_at=now,gmgn_alerts=alarms,web3_signals=display_rows[:DISPLAY_LIMIT],special_alerts=special,
            web3_health=health,web3_enabled=web_enabled,gmgn_health=store_value(con,'gmgn_market',{}),
            current_gmgn_count=len(candidates),current_web3_count=len(current),
            rules=dict(graduate_volume=cfg['graduate_volume'],other_volume=cfg['other_volume'],kol_count=cfg['kol_count'],
                       participant_mode=cfg.get('participant_mode'),participant_min=cfg.get('participant_min',1),
                       extra_30d_rule=cfg.get('extra_30d_rule',{}),
                       market_cap_interval_seconds=g.market_caps.INTERVAL,
                       web3_chains=list(CHAINS),web3_poll_seconds=POLL_SECONDS,special_poll_seconds=PUBLISH_SECONDS,
                       gmgn_fresh_seconds=GMGN_FRESH,web3_fresh_seconds=WEB3_FRESH))
        value['rules'].update(match_mode='alarm_pool_10d',alarm_pool_days=10,
                             match_on_each_source_update=True,matched_event_is_permanent=True,match_engine_version=POOL_VERSION)
        con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('signal_dashboard',json.dumps(value,ensure_ascii=False,allow_nan=False)))
        con.execute('DELETE FROM wallet_signals WHERE triggered<?',(now-7*86400,))
    return value


def store_value(con,key,default):
    row=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def dashboard():
    import gmgn_monitor as g
    now=time.time()
    with store.connect() as con:
        value=store_value(con,'signal_dashboard',{})
        # Sound used to read the live ledger while this card waited for a
        # 60-second publication. Overlay only GMGN from committed rows, leaving
        # the Web3 snapshot, source cadence and special matching policy intact.
        value['gmgn_alerts']=g.alarm_history(con,limit=DISPLAY_LIMIT,now=now,include_market_cap=True)
        # Read the latest retained special records too, even while an older
        # background publisher still emits its previous 20-item snapshot.
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='special_alerts' AND type='table'").fetchone():
            value['special_alerts']=special_rows(con)
        value['gmgn_health']=store_value(con,'gmgn_market',{})
        value['gmgn_view_read_at']=now
    return value


class Monitor:
    def __init__(self,client=None):self.client=client or Client()
    def step(self,now=None):
        import signal_ingest
        signal_ingest.drain_all()
        now=time.time() if now is None else now
        health=store.get('web3_signal_health',{});health.setdefault('chains',{})
        if configuration()['enabled']:
            for chain in CHAINS:
                old=health['chains'].get(chain,{})
                if now<old.get('next_poll_at',0):continue
                try:
                    cursor=max(2,int(old.get('scan_next_page',2)));seen=set();count=0
                    for batch in range(MAX_PAGES_PER_CYCLE):
                        page=1 if batch==0 else cursor+batch-1
                        rows=self.client.page(chain,page);stamp=time.time()
                        identities={(r['chain'],r['signal_id']) for r in rows}
                        no_progress=bool(rows) and not (identities-seen)
                        seen.update(identities);count=len(seen)
                        full=len(rows)==100
                        state=dict(updated=stamp,last_attempt=now,next_poll_at=max(now+POLL_SECONDS,stamp+1),
                            count=count,capped=full or no_progress,pages=batch+1,error='',failures=0,
                            scan_next_page=(cursor if batch==0 else page+1) if full else 2,
                            coverage_status='pagination_no_progress' if no_progress else ('continuing' if full else 'complete'))
                        if no_progress:
                            state['scan_next_page']=2;health['chains'][chain]=state;break
                        health['chains'][chain]=state
                        # Save and match EACH accepted page before requesting another;
                        # a later-page failure cannot discard already observed events.
                        if not record_wallet_batch(chain,rows,stamp,state):
                            raise SignalError('钱包信号已缓存待入库，稍后重试',60)
                        if not full:break
                except Exception as exc:
                    failures=old.get('failures',0)+1
                    wait=max(exc.wait if isinstance(exc,SignalError) else 30,min(300,30*2**min(failures-1,4)))
                    health['chains'][chain]=health['chains'].get(chain,old)|dict(last_attempt=now,next_poll_at=time.time()+wait,failures=failures,
                        error=str(exc) if isinstance(exc,SignalError) else '币安钱包信号暂不可用，保留缓存')
        health.update(heartbeat=time.time(),enabled=bool(configuration()['enabled']))
        store.put('web3_signal_health',health)
        snapshot=dashboard()
        if time.time()-snapshot.get('generated_at',0)>=PUBLISH_SECONDS:return publish(time.time())
        return snapshot


@contextmanager
def exclusive_worker():
    store.USER.mkdir(exist_ok=True)
    with (store.USER/'web3-signal-worker.lock').open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def run():
    with exclusive_worker():
        monitor=Monitor()
        while True:
            try:monitor.step()
            except Exception:print('钱包信号汇总暂不可用，稍后重试',flush=True)
            time.sleep(3)


if __name__=='__main__':run()
