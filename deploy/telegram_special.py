"""Server-only special-event delivery. Never logs tokens, URLs or chat contents."""
from datetime import datetime, timezone, timedelta
import html
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import time
from urllib.parse import quote
import requests

BASE=Path('/opt/perp-workbench')
SOURCE=BASE/'shared/user_data/workbench.sqlite'
STATE=BASE/'shared/telegram/delivery.sqlite'
TZ=timezone(timedelta(hours=8))


def binding_chats(updates,now,verified_start=None,bind_command=None):
    """Optional exact start timestamp must first be verified against the human's UI."""
    matched=set()
    for update in updates:
        msg=update.get('message',{});chat=msg.get('chat',{});sender=msg.get('from',{})
        text=str(msg.get('text','')).strip();date=msg.get('date',0)
        authorized=((bool(bind_command) and text==bind_command) or
                    (verified_start is not None and date==verified_start and text=='/start'))
        if (authorized and chat.get('type')=='private' and chat.get('id')==sender.get('id')
            and not sender.get('is_bot') and isinstance(chat.get('id'),int)
            and chat['id']>0 and 0<=now-date<7200):matched.add(chat['id'])
    return matched


class ApiError(Exception):
    def __init__(self,code,wait=0,ambiguous=False):
        self.code=code;self.wait=wait;self.ambiguous=ambiguous
        super().__init__('telegram_api_'+str(code))


def api(token,method,payload):
    if method not in ('getMe','getWebhookInfo','getUpdates','sendMessage'):
        raise ValueError('unsupported_method')
    try:
        with requests.post('https://api.telegram.org/bot'+token+'/'+method,json=payload,
                           timeout=(5,20),allow_redirects=False) as response:
            try:data=response.json()
            except ValueError:raise ApiError(response.status_code,ambiguous=method=='sendMessage') from None
            if response.status_code==200 and data.get('ok') is True:return data.get('result')
            code=data.get('error_code',response.status_code)
            wait=data.get('parameters',{}).get('retry_after',0)
            try:wait=max(0,int(wait))
            except (TypeError,ValueError):wait=60
            raise ApiError(code,wait)
    except requests.RequestException:
        # Telegram has no idempotency key. An interrupted send may have arrived;
        # quarantine it instead of replaying it and making duplicate alarms.
        raise ApiError('network',ambiguous=method=='sendMessage') from None


def state_db(path=STATE):
    con=sqlite3.connect(path,timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS delivery(event_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,
        status TEXT NOT NULL,attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,
        sent_at REAL,message_id INTEGER,last_error TEXT);''')
    return con


def source_db(path=SOURCE):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)


def money(value):
    if isinstance(value,bool):return '未知'
    try:value=float(value)
    except (TypeError,ValueError):return '未知'
    if not math.isfinite(value) or value<0:return '未知'
    for divisor,suffix in ((1e9,'B'),(1e6,'M'),(1e3,'K')):
        if value>=divisor:return f'${value/divisor:,.2f}{suffix}'
    return f'${value:,.2f}'


def message(row,cap=None,cap_time=None):
    ident,chain,address,triggered,raw=row
    item=json.loads(raw);token=item['token']
    if chain not in ('sol','bsc','base','robinhood') or token.get('chain')!=chain or token.get('address')!=address:
        raise ValueError('event_identity')
    pattern=r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain=='sol' else r'0x[0-9a-fA-F]{40}'
    if not re.fullmatch(pattern,address):raise ValueError('event_address')
    name=str(token.get('name') or token.get('symbol') or '未命名')[:100]
    symbol=str(token.get('symbol') or '')[:50]
    if symbol and symbol!=name:name+=' ('+symbol+')'
    stamp=datetime.fromtimestamp(triggered,TZ).strftime('%Y-%m-%d %H:%M:%S')
    cap_value=cap if cap_time else token.get('market_cap')
    cap_label='市值' if cap_time else '触发时市值'
    cap_note=('（更新 '+datetime.fromtimestamp(cap_time,TZ).strftime('%H:%M:%S')+'）') if cap_time else ''
    url='https://gmgn.ai/'+chain+'/token/'+quote(address,safe='')
    text=('🚨 <b>特级警报 · GMGN × Web3</b>\n'
          f'<b>{html.escape(name)}</b> · {html.escape(chain.upper())}\n'
          f'时间：{stamp} 北京时间\n'
          f'CA：<code>{html.escape(address)}</code>\n'
          f'{cap_label}：<b>{money(cap_value)}</b>{cap_note}\n'
          f'5分钟成交额：{money(token.get("volume"))}\n'
          f'<a href="{html.escape(url,quote=True)}">打开 GMGN</a>')
    return dict(text=text,parse_mode='HTML',link_preview_options={'is_disabled':True},
                reply_markup={'inline_keyboard':[[{'text':'打开 GMGN','url':url}]]})


def initialize(db,source,now):
    if db.execute("SELECT 1 FROM meta WHERE key='cursor'").fetchone():return
    latest=source.execute('SELECT COALESCE(MAX(id),0) FROM special_alerts').fetchone()[0]
    with db:
        db.execute('INSERT INTO meta VALUES (?,?)',('cursor',str(latest)))
        db.execute('INSERT INTO meta VALUES (?,?)',('initialized_at',str(now)))


def enqueue(db,source,now):
    cursor=int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    rows=source.execute('SELECT id,chain,address,triggered,data FROM special_alerts WHERE id>? ORDER BY id LIMIT 100',(cursor,)).fetchall()
    with db:
        for row in rows:
            try:
                cap=source.execute('SELECT market_cap,observed FROM gmgn_market_caps WHERE chain=? AND address=?',(row[1],row[2])).fetchone()
                cap=cap if cap and cap[1] and 0<=now-cap[1]<=600 else (None,None)
                payload=message(row,*cap)
                db.execute('INSERT OR IGNORE INTO delivery(event_id,payload,status) VALUES (?,?,?)',
                           (row[0],json.dumps(payload,ensure_ascii=False),'pending'))
            except (ValueError,TypeError,KeyError,OverflowError):
                db.execute('INSERT OR IGNORE INTO delivery(event_id,payload,status,last_error) VALUES (?,?,?,?)',
                           (row[0],'{}','invalid','invalid_event'))
            db.execute("UPDATE meta SET value=? WHERE key='cursor'",(str(row[0]),))
    return len(rows)


def deliver(db,token,chat_id,now,call=api):
    blocked=db.execute("SELECT value FROM meta WHERE key='api_blocked_until'").fetchone()
    if blocked and now<float(blocked[0]):return
    row=db.execute("SELECT event_id,payload,attempts FROM delivery WHERE status='pending' AND next_try<=? ORDER BY event_id LIMIT 1",(now,)).fetchone()
    if not row:return
    ident,raw,attempts=row
    with db:db.execute("UPDATE delivery SET status='sending',attempts=attempts+1 WHERE event_id=?",(ident,))
    try:
        result=call(token,'sendMessage',json.loads(raw)|dict(chat_id=chat_id))
        if not isinstance(result,dict) or not isinstance(result.get('message_id'),int):raise ApiError('bad_result',ambiguous=True)
        with db:db.execute("UPDATE delivery SET status='sent',sent_at=?,message_id=?,last_error=NULL WHERE event_id=?",(now,result['message_id'],ident))
    except ApiError as exc:
        status='uncertain' if exc.ambiguous else ('failed' if exc.code in (400,401,403,404) else 'pending')
        wait=max(exc.wait,min(3600,30*2**min(attempts,7)))
        with db:
            db.execute('UPDATE delivery SET status=?,next_try=?,last_error=? WHERE event_id=?',(status,now+wait,str(exc.code),ident))
            if exc.code==429:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('api_blocked_until',str(now+wait)))
        print(json.dumps(dict(event='delivery_error',event_id=ident,code=exc.code,status=status)),flush=True)


def run():
    os.umask(0o077)
    token=os.environ['TELEGRAM_BOT_TOKEN'];chat_id=int(os.environ['TELEGRAM_CHAT_ID'])
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{30,100}',token) or chat_id<=0:raise ValueError('configuration')
    db=state_db()
    # A crash after the server accepted a send has an unknown outcome.
    with db:db.execute("UPDATE delivery SET status='uncertain',last_error='interrupted' WHERE status='sending'")
    stopping=False
    def stop(*args):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping:
        try:
            now=time.time()
            with source_db() as source:
                initialize(db,source,now);enqueue(db,source,now)
            deliver(db,token,chat_id,now)
            with db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('heartbeat',str(time.time())))
        except Exception:
            print('{"event":"worker_check_failed"}',flush=True)
        for _ in range(10):
            if stopping:break
            time.sleep(1)
    db.close()


if __name__=='__main__':run()
