"""Five-minute BlockBeats Pro news ingestion, independent of page refreshes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
import time
from urllib.parse import urlsplit, urljoin
import requests
import store
from blockbeats_credentials import api_key

BASE='https://api-pro.theblockbeats.info'
INTERVAL=300
MAX_PAGES=3
MAX_ITEMS=500
ZONE=timezone(timedelta(hours=8))


def configuration():
    return dict(enabled=True,auto_scroll=True,scroll_speed=12) | store.get('blockbeats_config',{})


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','iframe','object'):self.hidden+=1
        elif not self.hidden and tag in ('p','br','div','li'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','iframe','object'):self.hidden=max(0,self.hidden-1)
        elif not self.hidden and tag in ('p','div','li'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def plain(value,limit):
    parser=PlainText();parser.feed(str(value or '')[:100000])
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())[:limit]


def official_link(value):
    if not value:return ''
    try:
        url=urljoin('https://www.theblockbeats.info/',str(value or ''))
        parsed=urlsplit(url)
        host=(parsed.hostname or '').lower()
        if (parsed.scheme in ('https','http') and not parsed.username and not parsed.password
                and (host in ('theblockbeats.info','theblockbeats.news') or host.endswith(('.theblockbeats.info','.theblockbeats.news')))
                and parsed.port in (None,80,443)):
            return parsed._replace(scheme='https',netloc=host).geturl()
    except ValueError:pass
    return ''


def normalize(raw,observed):
    if not isinstance(raw,dict) or isinstance(raw.get('id'),bool):return None
    try:ident=int(raw['id'])
    except (ValueError,TypeError,KeyError):return None
    if not 0<ident<2**63:return None
    title=plain(raw.get('title'),500)
    if not title:return None
    source_time=str(raw.get('create_time') or '')[:40]
    try:
        date=datetime.fromisoformat(source_time)
        if date.tzinfo is None:date=date.replace(tzinfo=ZONE)
        published=date.timestamp()
    except (ValueError,OverflowError):published=None
    return dict(id=ident,title=title,content=plain(raw.get('content'),16000),
                link=official_link(raw.get('link')),published_at=published,
                published_text=source_time,observed=observed)


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS blockbeats_news (
        id INTEGER PRIMARY KEY,title TEXT,content TEXT,link TEXT,
        published_at REAL,published_text TEXT,observed REAL)''')


def recent(limit=100):
    with store.connect() as con:
        schema(con)
        con.row_factory=__import__('sqlite3').Row
        return [dict(r) for r in con.execute('SELECT * FROM blockbeats_news ORDER BY published_at DESC,id DESC LIMIT ?',
                                             (max(1,min(int(limit),MAX_ITEMS)),))]


class NewsError(Exception):
    def __init__(self,message,wait=INTERVAL):
        super().__init__(message);self.wait=max(INTERVAL,wait)


class Client:
    def __init__(self):self.session=requests.Session()
    def page(self,page):
        try:key=api_key()
        except (OSError,ValueError):raise NewsError('本机律动密钥暂不可读取，请使用保存密钥的Windows账户启动')
        if not key:raise NewsError('等待配置律动API Key')
        try:
            with self.session.get(BASE+'/v1/newsflash',params=dict(page=page,size=50,lang='cn'),
                                  headers={'api-key':key},timeout=(10,20),allow_redirects=False,stream=True) as response:
                if response.status_code in (401,403):raise NewsError('律动密钥或套餐权限不可用，请在设置页检查',1800)
                if response.status_code==429:
                    retry=response.headers.get('Retry-After','')
                    raise NewsError('律动接口限流，等待重试',min(86400,int(retry)) if retry.isdigit() else INTERVAL)
                if response.status_code!=200:raise NewsError('律动接口暂不可用，稍后自动重试')
                chunks=[];size=0
                for part in response.iter_content(65536):
                    size+=len(part)
                    if size>2*1024*1024:raise NewsError('律动返回内容超过安全大小，保留已有快讯')
                    chunks.append(part)
                payload=json.loads(b''.join(chunks))
        except (requests.RequestException,ValueError):raise NewsError('律动连接或返回格式异常，稍后自动重试') from None
        if not isinstance(payload,dict) or payload.get('status')!=0:
            raise NewsError('律动返回接口错误，保留已有快讯')
        data=payload.get('data')
        if not isinstance(data,dict) or str(data.get('page'))!=str(page) or not isinstance(data.get('data'),list) or len(data['data'])>50:
            raise NewsError('律动快讯字段变化，保留已有快讯')
        raw=data['data'];observed=time.time()
        rows=[item for item in (normalize(r,observed) for r in raw) if item]
        if len(rows)!=len(raw):raise NewsError('律动快讯字段不完整，保留已有快讯')
        return rows


class Monitor:
    def __init__(self,client=None):self.client=client or Client()
    def step(self,now=None):
        now=time.time() if now is None else now
        cfg=configuration();health=store.get('blockbeats_health',{})
        if not cfg['enabled']:
            if health.get('enabled') is not False or now-health.get('heartbeat',0)>=30:
                store.put('blockbeats_health',health|dict(enabled=False,heartbeat=now,status='已暂停'))
            return
        request=store.get('blockbeats_refresh_request','')
        if now<health.get('next_poll_at',0) and request==health.get('refresh_request',''):
            if now-health.get('heartbeat',0)>=30:store.put('blockbeats_health',health|dict(heartbeat=now,enabled=True))
            return
        known={r['id'] for r in recent(MAX_ITEMS)};items={};limited=False
        try:
            for page in range(1,MAX_PAGES+1):
                rows=self.client.page(page)
                items.update({r['id']:r for r in rows})
                if not known or len(rows)<50 or any(r['id'] in known for r in rows):break
                if page==MAX_PAGES:limited=True
            finished=time.time()
            with store.connect() as con:
                schema(con)
                con.executemany('INSERT OR REPLACE INTO blockbeats_news VALUES (?,?,?,?,?,?,?)',
                    [(r['id'],r['title'],r['content'],r['link'],r['published_at'],r['published_text'],r['observed']) for r in items.values()])
                con.execute('DELETE FROM blockbeats_news WHERE observed<?',(finished-2*86400,))
                con.execute('DELETE FROM blockbeats_news WHERE id NOT IN (SELECT id FROM blockbeats_news ORDER BY published_at DESC,id DESC LIMIT ?)',(MAX_ITEMS,))
            store.put('blockbeats_health',health|dict(heartbeat=finished,last_success=finished,enabled=True,
                status='已更新',error='',next_poll_at=max(now+INTERVAL,finished),refresh_request=request,
                fetched=len(items),new_count=len(set(items)-known),coverage_limited=limited))
        except Exception as error:
            message=str(error) if isinstance(error,NewsError) else '律动采集暂不可用，保留已有快讯'
            wait=error.wait if isinstance(error,NewsError) else INTERVAL
            store.put('blockbeats_health',health|dict(heartbeat=time.time(),enabled=True,status='等待重试',error=message,
                next_poll_at=time.time()+wait,refresh_request=request))


@contextmanager
def exclusive_worker():
    store.USER.mkdir(exist_ok=True)
    with (store.USER/'blockbeats-worker.lock').open('a+b') as stream:
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
            except Exception:pass  # A local database failure must not exit the supervisor's child.
            time.sleep(2)


if __name__=='__main__':run()
