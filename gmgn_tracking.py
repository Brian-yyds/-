"""Durable first-alert windows, Beijing daily caps and actual 1h-volume rankings."""
import json
import time
from datetime import datetime
import store

WINDOW_SECONDS = 10*86400
FRESH_SECONDS = 180
INFO_INTERVAL = 60
MAX_INFO_PER_CYCLE = 20


def identity(chain,address):
    return chain,address if chain=='sol' else address.lower()


def day(stamp):
    from gmgn_monitor import ZONE
    return datetime.fromtimestamp(stamp,ZONE).strftime('%Y-%m-%d')


def schema(con):
    import gmgn_monitor as g
    con.execute('''CREATE TABLE IF NOT EXISTS gmgn_watch (
        chain TEXT,address TEXT,first_alert REAL,expires_at REAL,last_alert REAL,initial_rule TEXT,
        token TEXT,last_seen REAL,volume_1h REAL,volume_observed REAL DEFAULT 0,
        info_attempt REAL DEFAULT 0,info_retry REAL DEFAULT 0,PRIMARY KEY(chain,address))''')
    con.execute('CREATE TABLE IF NOT EXISTS gmgn_daily (chain TEXT,address TEXT,day TEXT,alert_id INTEGER,PRIMARY KEY(chain,address,day))')
    con.execute('CREATE TABLE IF NOT EXISTS gmgn_track_meta (key TEXT PRIMARY KEY,value INTEGER)')
    old=con.execute("SELECT value FROM gmgn_track_meta WHERE key='seed_id'").fetchone()
    rows=con.execute('SELECT id,chain,address,rule,triggered,last_seen,data FROM gmgn_alerts WHERE id>? ORDER BY id',(old[0] if old else 0,)).fetchall()
    for ident,chain,address,rule,triggered,seen,data in rows:
        token=json.loads(data)
        if not g.valid_address(chain,address) or g.equities.equity_reason(token):continue
        chain,address=identity(chain,address)
        con.execute('''INSERT INTO gmgn_watch(chain,address,first_alert,expires_at,last_alert,initial_rule,token,last_seen)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chain,address) DO UPDATE SET
            first_alert=MIN(first_alert,excluded.first_alert),expires_at=MIN(expires_at,excluded.expires_at),
            initial_rule=CASE WHEN excluded.first_alert<first_alert THEN excluded.initial_rule ELSE initial_rule END,
            last_alert=MAX(last_alert,excluded.last_alert),token=CASE WHEN excluded.last_seen>=last_seen THEN excluded.token ELSE token END,
            last_seen=MAX(last_seen,excluded.last_seen)''',(chain,address,triggered,triggered+WINDOW_SECONDS,triggered,rule,data,seen))
        con.execute('INSERT INTO gmgn_daily VALUES (?,?,?,?) ON CONFLICT(chain,address,day) DO UPDATE SET alert_id=MAX(alert_id,excluded.alert_id)',
                    (chain,address,day(triggered),ident))
    if rows:con.execute("INSERT OR REPLACE INTO gmgn_track_meta VALUES ('seed_id',?)",(rows[-1][0],))


def record_scan(chain,tokens,cfg,stamp):
    import gmgn_monitor as g
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        g.schema(con)
        for incoming in tokens:
            if not isinstance(incoming,dict):continue
            token=dict(incoming)
            if token.get('chain')!=chain or not g.valid_address(chain,token.get('address')) or g.equities.equity_reason(token):continue
            chain_key,address=identity(chain,token['address']);token['address']=address
            watch=con.execute('SELECT first_alert,expires_at,initial_rule,last_seen FROM gmgn_watch WHERE chain=? AND address=?',(chain_key,address)).fetchone()
            if watch and (stamp>=watch[1] or stamp<watch[3]):continue
            followup=bool(watch and watch[2] in ('今日毕业放量','毕业代币再次放量'))
            rule=g.reason(token,cfg,stamp,followup=followup)
            old=con.execute('SELECT active,last_true,below_since,observed FROM gmgn_episodes WHERE chain=? AND address=? ORDER BY observed DESC LIMIT 1',(chain_key,address)).fetchone()
            if old and stamp<old[3]:continue
            active=bool(old and old[0]);last_true=old[1] if old else 0;below=old[2] if old else None
            sent=False
            if rule:
                sent_today=con.execute('SELECT 1 FROM gmgn_daily WHERE chain=? AND address=? AND day=?',(chain_key,address,day(stamp))).fetchone()
                if not active and not sent_today:
                    event=con.execute('INSERT INTO gmgn_alerts(chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?)',
                                      (chain_key,address,rule,stamp,stamp,json.dumps(token,ensure_ascii=False)))
                    con.execute('INSERT INTO gmgn_daily VALUES (?,?,?,?)',(chain_key,address,day(stamp),event.lastrowid))
                    if not watch:
                        con.execute('INSERT INTO gmgn_watch(chain,address,first_alert,expires_at,last_alert,initial_rule,token,last_seen) VALUES (?,?,?,?,?,?,?,?)',
                                    (chain_key,address,stamp,stamp+WINDOW_SECONDS,stamp,rule,json.dumps(token,ensure_ascii=False),stamp))
                    else:con.execute('UPDATE gmgn_watch SET last_alert=? WHERE chain=? AND address=?',(stamp,chain_key,address))
                    sent=True
                if not sent:
                    con.execute('UPDATE gmgn_alerts SET last_seen=?,data=? WHERE id=(SELECT MAX(id) FROM gmgn_alerts WHERE chain=? AND address=?)',
                                (stamp,json.dumps(token,ensure_ascii=False),chain_key,address))
                active=True;last_true=stamp;below=None
            else:
                valid=token.get('volume') is not None and g.launch_kind(token)!='unknown'
                if active and valid:
                    threshold=cfg['graduate_volume'] if followup or g.launch_kind(token)=='launchpad' else cfg['other_volume']
                    if token['volume']>threshold:below=None
                    else:
                        below=below if below is not None and old and stamp-old[3]<=120 else stamp
                        if stamp-below>=300:active=False
            # Canonical per-token episode, shared by both rule branches and daily cap.
            con.execute('DELETE FROM gmgn_episodes WHERE chain=? AND address=? AND rule!=?',(chain_key,address,'volume'))
            con.execute('INSERT OR REPLACE INTO gmgn_episodes VALUES (?,?,?,?,?,?,?)',(chain_key,address,'volume',int(active),last_true,below,stamp))
            con.execute('UPDATE gmgn_watch SET token=?,last_seen=? WHERE chain=? AND address=?',
                        (json.dumps(token,ensure_ascii=False),stamp,chain_key,address))
        con.execute('DELETE FROM gmgn_alerts WHERE acknowledged=1 AND triggered<? AND id NOT IN (SELECT id FROM gmgn_alerts ORDER BY triggered DESC,id DESC LIMIT 3)',(stamp-30*86400,))


def watch_rows(now=None):
    import gmgn_monitor as g
    now=time.time() if now is None else now
    with store.connect() as con:
        g.schema(con)
        rows=con.execute('SELECT chain,address,first_alert,expires_at,last_alert,token,volume_1h,volume_observed,info_attempt,info_retry FROM gmgn_watch WHERE first_alert<=? AND expires_at>? ORDER BY info_attempt,first_alert',(now,now)).fetchall()
    names=('chain','address','first_alert','expires_at','last_alert','token','volume_1h','volume_observed','info_attempt','info_retry')
    result=[]
    for values in rows:
        row=dict(zip(names,values));row['token']=json.loads(row['token'])
        if not g.equities.equity_reason(row['token']):result.append(row)
    return result


def ranking_snapshot(now=None):
    now=time.time() if now is None else now
    watched=watch_rows(now)
    fresh=[r for r in watched if r['volume_1h'] is not None and 0<=now-r['volume_observed']<=FRESH_SECONDS]
    fresh.sort(key=lambda r:(-r['volume_1h'],r['first_alert'],r['chain'],r['address']))
    return dict(window_days=10,anchor='first_alert',volume_window='1h',currency='USD',generated_at=now,
                daily_timezone='Asia/Shanghai',daily_limit=1,total=len(watched),fresh=len(fresh),complete=len(fresh)==len(watched),
                fresh_max_age_seconds=FRESH_SECONDS,rows=fresh[:10])


def parse_info(raw,chain,address,stamp):
    import gmgn_monitor as g
    if isinstance(raw,dict) and 'code' in raw:
        if raw.get('code')!=0:raise ValueError('Token info error')
        raw=raw.get('data')
    if not isinstance(raw,dict) or identity(chain,str(raw.get('address','')))!=identity(chain,address):raise ValueError('Token info identity mismatch')
    price=raw.get('price');tags=raw.get('wallet_tags_stat',{})
    if not isinstance(price,dict) or not isinstance(tags,dict):raise ValueError('Token info shape changed')
    data=raw|dict(volume=price.get('volume_5m'),smart_degen_count=tags.get('smart_wallets'),renowned_count=tags.get('renowned_wallets'))
    price_value=g.number(price.get('price'));supply=g.number(raw.get('circulating_supply'))
    data['market_cap']=price_value*supply if price_value is not None and supply is not None else None
    token=g.normalize(data,chain,stamp)
    return token,g.number(price.get('volume_1h'))


def refresh_info(client,cfg,now=None):
    import gmgn_monitor as g
    now=time.time() if now is None else now
    due=[r for r in watch_rows(now) if now>=r['info_retry'] and now-r['info_attempt']>=INFO_INTERVAL][:MAX_INFO_PER_CYCLE]
    for start in range(0,len(due),10):
        group=due[start:start+10]
        attempted=time.time()
        with store.connect() as con:
            con.executemany('UPDATE gmgn_watch SET info_attempt=?,info_retry=? WHERE chain=? AND address=?',
                            [(attempted,attempted+INFO_INTERVAL,r['chain'],r['address']) for r in group])
        result=client.query(['token','info-batch','--params',json.dumps([dict(chain=r['chain'],address=r['address']) for r in group])])
        if not isinstance(result,list):raise g.QueryError('GMGN 小时数据格式变化，稍后重试')
        expected={identity(r['chain'],r['address']) for r in group};seen=set()
        for item in result:
            if not isinstance(item,dict):continue
            key=identity(str(item.get('chain','')),str(item.get('address','')))
            if key not in expected or key in seen:continue
            seen.add(key)
            if not item.get('ok'):continue
            observed=g.number(item.get('observed'))
            if observed is None or not attempted-1<=observed<=time.time()+2:continue
            try:token,volume=parse_info(item.get('data'),key[0],key[1],observed)
            except (TypeError,ValueError):continue
            record_scan(key[0],[token],cfg,observed)
            with store.connect() as con:
                if volume is not None:
                    con.execute('UPDATE gmgn_watch SET volume_1h=?,volume_observed=? WHERE chain=? AND address=? AND expires_at>? AND volume_observed<=?',
                                (volume,observed,key[0],key[1],observed,observed))
