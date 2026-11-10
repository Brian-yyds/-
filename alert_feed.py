"""Server-side read-only special alarm feed; independent of collectors/bots."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import ssl
import threading
import time
from urllib.parse import parse_qs,urlsplit,quote

TZ=timezone(timedelta(hours=8))
CHAINS=('sol','bsc','base','robinhood')


def number(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and value>=0 else None


def iso(stamp):return datetime.fromtimestamp(stamp,TZ).isoformat(timespec='seconds')


def money(value):
    if value is None:return '未知'
    return f'${value:,.2f}'


def serialize(row,feed_id,cap,now):
    ident,chain,address,stamp,raw=row
    if len(raw)>65536:raise ValueError('oversized_event')
    item=json.loads(raw)
    if not isinstance(item,dict) or not isinstance(item.get('token'),dict):raise ValueError('event_shape')
    token=item['token']
    pattern=r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain=='sol' else r'0x[0-9a-fA-F]{40}'
    if (chain not in CHAINS or not re.fullmatch(pattern,address) or token.get('chain')!=chain
        or token.get('address')!=address or number(stamp) is None):raise ValueError('event_identity')
    name=str(token.get('name') or token.get('symbol') or '未命名')[:150]
    symbol=str(token.get('symbol') or '')[:80]
    name=''.join(c for c in name if c.isprintable());symbol=''.join(c for c in symbol if c.isprintable())
    market_cap=number(token.get('market_cap'));cap_source='at_alert';cap_at=stamp
    if cap and number(cap[0]) is not None and number(cap[1]) is not None and 0<=now-cap[1]<=600:
        market_cap=number(cap[0]);cap_source='current';cap_at=cap[1]
    url='https://gmgn.ai/'+chain+'/token/'+quote(address,safe='')
    volume=number(token.get('volume'))
    text=('🚨 特级警报 · GMGN × Web3\n'+name+(f' ({symbol})' if symbol and symbol!=name else '')+
          f' · {chain.upper()}\n报警时间：{iso(stamp)}\nCA：{address}\n'+
          ('市值' if cap_source=='current' else '触发时市值')+'：'+money(market_cap)+
          f'\n5分钟成交额：{money(volume)}\nGMGN：{url}')
    return dict(event_id=f'{feed_id}:special:{ident}',id=ident,type='special',
                sources=['gmgn','binance_web3'],chain=chain,name=name,symbol=symbol,ca=address,
                triggered_at=iso(stamp),triggered_unix=stamp,market_cap_usd=market_cap,
                market_cap_source=cap_source,market_cap_observed_at=iso(cap_at),
                trigger_market_cap_usd=number(token.get('market_cap')),volume_5m_usd=volume,
                gmgn_url=url,text=text)


class Feed:
    def __init__(self,path,feed_id):self.path=Path(path);self.feed_id=feed_id

    def connect(self):
        con=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
        con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        return con

    def latest(self):
        con=self.connect()
        try:latest=con.execute('SELECT COALESCE(MAX(id),0) FROM special_alerts').fetchone()[0]
        finally:con.close()
        return dict(version=1,feed_id=self.feed_id,latest_id=latest,server_time=iso(time.time()))

    def events(self,after_id=0,limit=50,now=None):
        now=time.time() if now is None else now
        con=self.connect()
        try:
            latest=con.execute('SELECT COALESCE(MAX(id),0) FROM special_alerts').fetchone()[0]
            if after_id>latest:return 409,dict(error='cursor_ahead',feed_id=self.feed_id,latest_id=latest)
            rows=con.execute('SELECT id,chain,address,triggered,data FROM special_alerts WHERE id>? ORDER BY id LIMIT ?',
                             (after_id,limit)).fetchall()
            has_caps=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gmgn_market_caps'").fetchone()
            items=[];invalid=[]
            for row in rows:
                try:
                    cap=con.execute('SELECT market_cap,observed FROM gmgn_market_caps WHERE chain=? AND address=?',
                                    (row[1],row[2])).fetchone() if has_caps else None
                    items.append(serialize(row,self.feed_id,cap,now))
                except (ValueError,TypeError,KeyError,OverflowError):invalid.append(row[0])
        finally:con.close()
        cursor=rows[-1][0] if rows else after_id
        return 200,dict(version=1,feed_id=self.feed_id,server_time=iso(now),items=items,
                        next_after_id=cursor,latest_id=latest,has_more=cursor<latest,invalid_ids=invalid)

    def health(self):
        result=self.latest();con=self.connect();now=time.time()
        try:
            states={}
            for key in ('gmgn_market','web3_signal_health'):
                row=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
                data=json.loads(row[0]) if row else {}
                stamp=number(data.get('heartbeat'))
                states[key]=dict(heartbeat_age_seconds=round(now-stamp,1) if stamp else None)
            result.update(status='ok',sources=states)
        finally:con.close()
        return result


class Handler(BaseHTTPRequestHandler):
    server_version='AlertFeed/1';sys_version=''
    def log_message(self,*args):pass  # Do not log credentials or query strings.

    def reply(self,status,data):
        body=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close')
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        auth=self.headers.get('Authorization','')
        if not hmac.compare_digest(hashlib.sha256(auth.encode()).digest(),self.server.auth_hash):
            return self.reply(401,dict(error='unauthorized'))
        parsed=urlsplit(self.path)
        try:
            if parsed.path=='/v1/alerts':
                args=parse_qs(parsed.query,keep_blank_values=True)
                if set(args)-{'after_id','limit'} or any(len(v)!=1 for v in args.values()):raise ValueError()
                def integer(name,default,maximum):
                    raw=args.get(name,[str(default)])[0]
                    if not re.fullmatch(r'\d{1,19}',raw):raise ValueError()
                    value=int(raw)
                    if value>maximum:raise ValueError()
                    return value
                after=integer('after_id',0,2**63-1);limit=integer('limit',50,100)
                if limit<1:raise ValueError()
                status,data=self.server.feed.events(after,limit)
            elif parsed.path in ('/v1/alerts/latest','/v1/health'):
                if parsed.query:raise ValueError()
                status=200;data=self.server.feed.latest() if parsed.path.endswith('/latest') else self.server.feed.health()
            else:return self.reply(404,dict(error='not_found'))
        except ValueError:return self.reply(400,dict(error='invalid_parameters'))
        except (sqlite3.Error,OSError):return self.reply(503,dict(error='source_unavailable'))
        return self.reply(status,data)

    def do_POST(self):self.reply(405,dict(error='read_only'))
    do_PUT=do_POST;do_DELETE=do_POST;do_PATCH=do_POST


class Server(HTTPServer):
    def __init__(self,address,feed,token,context):
        self.feed=feed;self.auth_hash=hashlib.sha256(('Bearer '+token).encode()).digest();self.context=context
        self.slots=threading.BoundedSemaphore(16);self.pool=ThreadPoolExecutor(max_workers=16)
        super().__init__(address,Handler)

    def get_request(self):
        sock,addr=super().get_request();sock.settimeout(5);return sock,addr

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        self.pool.submit(self.handle_request_socket,request,address)

    def handle_request_socket(self,request,address):
        try:
            if self.context:request=self.context.wrap_socket(request,server_side=True)
            self.finish_request(request,address)
        except (OSError,ValueError):pass
        finally:self.shutdown_request(request);self.slots.release()

    def server_close(self):super().server_close();self.pool.shutdown(wait=True)


def main():
    token=os.environ['ALERT_FEED_TOKEN'];feed_id=os.environ['ALERT_FEED_ID']
    if len(token)<32 or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',feed_id):raise ValueError('feed_configuration')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(os.environ['ALERT_FEED_CERT'],os.environ['ALERT_FEED_KEY'])
    feed=Feed(os.environ['ALERT_FEED_DB'],feed_id)
    with Server(('0.0.0.0',int(os.environ.get('ALERT_FEED_PORT','9443'))),feed,token,context) as server:
        print(json.dumps(dict(event='alert_feed_started',version=1)),flush=True)
        server.serve_forever(poll_interval=.5)


if __name__=='__main__':main()
