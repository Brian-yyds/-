"""Bot-side durable inbox. Fetch/queue only; the receiving bot sends to WeChat."""
import argparse
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler,ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None


def fetch(config,path):
    base=config['base_url'].rstrip('/');url=urlsplit(base)
    if url.scheme!='https' or url.username or url.password or url.query or url.fragment:raise ValueError('https_configuration_required')
    context=ssl.create_default_context(cafile=config['ca_file'])
    # The private feed is reached directly; do not route its token through an
    # unrelated workstation HTTP proxy or accept that proxy's TLS certificate.
    opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),NoRedirect())
    req=Request(base+path,headers={'Authorization':'Bearer '+config['token'],'Accept':'application/json'})
    with opener.open(req,timeout=15) as response:
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('response_too_large')
        return json.loads(raw)


def database(path):
    con=sqlite3.connect(path,timeout=5)
    con.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,delivered INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS rejected(id INTEGER PRIMARY KEY,reason TEXT NOT NULL);''')
    return con


def poll(con,config,replay=False,request=fetch):
    values=dict(con.execute('SELECT key,value FROM meta'))
    if not values:
        first=request(config,'/v1/alerts/latest')
        if not isinstance(first.get('feed_id'),str) or type(first.get('latest_id')) is not int or first['latest_id']<0:raise ValueError('invalid_feed_metadata')
        if config.get('feed_id') and first['feed_id']!=config['feed_id']:raise ValueError('feed_identity_changed_review_required')
        with con:
            con.executemany('INSERT INTO meta VALUES (?,?)',[('feed_id',first['feed_id']),('cursor','0' if replay else str(first['latest_id']))])
        values=dict(con.execute('SELECT key,value FROM meta'))
    cursor=int(values['cursor']);data=request(config,f'/v1/alerts?after_id={cursor}&limit=100')
    if data.get('feed_id')!=values['feed_id']:raise ValueError('feed_identity_changed_review_required')
    next_id=data.get('next_after_id');latest=data.get('latest_id')
    if type(next_id) is not int or type(latest) is not int or not cursor<=next_id<=latest:raise ValueError('invalid_cursor')
    if not isinstance(data.get('items'),list) or not isinstance(data.get('invalid_ids'),list):raise ValueError('invalid_feed_page')
    ids=set()
    with con:
        for item in data['items']:
            ident=item.get('id');event_id=item.get('event_id')
            if (type(ident) is not int or not cursor<ident<=next_id or ident in ids or
                event_id!=f'{values["feed_id"]}:special:{ident}' or item.get('type')!='special'):
                raise ValueError('invalid_event')
            ids.add(ident)
            con.execute('INSERT OR IGNORE INTO inbox(event_id,payload) VALUES (?,?)',(event_id,json.dumps(item,ensure_ascii=False)))
        for ident in data['invalid_ids']:
            if type(ident) is not int or not cursor<ident<=next_id or ident in ids:raise ValueError('invalid_rejected_id')
            ids.add(ident)
            con.execute('INSERT OR IGNORE INTO rejected VALUES (?,?)',(ident,'invalid_source_event'))
        if next_id>cursor and next_id not in ids:raise ValueError('unaccounted_cursor')
        con.execute("UPDATE meta SET value=? WHERE key='cursor'",(str(next_id),))
    return dict(queued=len(data['items']),has_more=bool(data.get('has_more')),invalid_ids=data['invalid_ids'])


def pending(con):
    return [json.loads(row[0]) for row in con.execute('SELECT payload FROM inbox WHERE delivered=0 ORDER BY rowid')]


def acknowledge(con,event_id):
    with con:con.execute('UPDATE inbox SET delivered=1 WHERE event_id=?',(event_id,))


def main():
    parser=argparse.ArgumentParser(description='拉取服务器特级警报到本地待发送队列，不直接发微信')
    parser.add_argument('--config',default='client-config.json');parser.add_argument('--state',default='bot-alert-inbox.sqlite')
    parser.add_argument('--replay-history',action='store_true',help='仅首次初始化时，从完整历史开始')
    parser.add_argument('--ack',metavar='EVENT_ID',help='微信发送成功后确认这条事件')
    parser.add_argument('--watch',action='store_true',help='每15秒轮询，输出仍未确认的事件')
    args=parser.parse_args();path=Path(args.config).resolve();config=json.loads(path.read_text(encoding='utf-8'))
    config['ca_file']=str(path.parent/config.get('ca_file','server-ca.pem'))
    con=database(args.state)
    try:
        if args.ack:acknowledge(con,args.ack);return
        failures=0
        while True:
            try:
                page=poll(con,config,args.replay_history)
                while page['has_more']:page=poll(con,config)
                for item in pending(con):print(json.dumps(item,ensure_ascii=False),flush=True)
                failures=0
            except OSError:
                if not args.watch:raise
                failures+=1
                print(json.dumps(dict(error='fetch_failed_cursor_kept',retry_seconds=min(300,15*2**min(failures-1,5)))),file=sys.stderr,flush=True)
            if not args.watch:break
            time.sleep(min(300,15*2**min(failures-1,5)) if failures else 15)
    finally:con.close()


if __name__=='__main__':main()
