"""Bounded, serial collection of two public Binance top-50 lists and operations."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

import requests
import copy_trader as source
import curated_traders
import smart_money

RANK_URL = 'https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/home-page/query-list'
RANK_INTERVAL = 900
ORDER_INTERVAL = 120
REQUEST_GAP = 1.0
THREAD_NAME = 'workbench-public-copy-feed'
COLLECTOR_VERSION = 4
CACHE = Path(__file__).resolve().parent / 'user_data' / 'copy_feed.sqlite'
_START_LOCK = threading.Lock()


def rank_page(session, kind, page):
    response = session.post(RANK_URL, json=dict(dataType=kind, favoriteOnly=False,
        hideFull=False, nickname='', order='DESC', pageNumber=page, pageSize=30,
        portfolioType='ALL', timeRange='30D', useAiRecommended=False, userAsset=0),
        timeout=(5,12), allow_redirects=False)
    if response.status_code in (401,403,418,429):
        try: retry=float(response.headers.get('Retry-After',0))
        except (TypeError,ValueError): retry=0
        raise source.SourceError('币安访问限制，暂停查询', max(900,retry if math.isfinite(retry) else 900))
    if response.status_code != 200:
        raise source.SourceError(f'币安榜单暂不可用（{response.status_code}）')
    payload=response.json()
    if not isinstance(payload,dict) or payload.get('code')!='000000' or payload.get('success') is False:
        raise source.SourceError('币安未提供公开榜单')
    data=payload.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('list'),list):
        raise source.SourceError('榜单格式异常')
    rows=[]
    for raw in data['list']:
        pid=str(raw.get('leadPortfolioId',''))
        roi,pnl=float(raw['roi']),float(raw['pnl'])
        if not pid.isdigit() or not 10<=len(pid)<=25 or not math.isfinite(roi) or not math.isfinite(pnl):
            raise source.SourceError('榜单缺少有效标识或收益数据')
        rows.append(dict(id=pid,name=str(raw.get('nickname') or pid)[:120],
                         roi=roi,pnl=pnl,portfolio_type=raw.get('portfolioType','UNKNOWN')))
    return rows,int(data.get('total',len(rows)))


def combine_pages(first, second, total):
    rows=(first+second)[:50]
    if len(rows)!=min(50,total) or len({r['id'] for r in rows})!=len(rows):
        raise source.SourceError('榜单分页覆盖不足，保留上次完整榜单')
    return [dict(row,rank=i) for i,row in enumerate(rows,1)]


class Monitor:
    def __init__(self,path=CACHE):
        self.path=Path(path)
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.state=dict(rankings={kind:dict(rows=[],updated=0,retry_at=0,error='') for kind in ['ROI','PNL']},
                        histories={},smart=dict(rows=[],profiles={},updated=0,retry_at=0,error=''),blocked_until=0,error='')
        self._load()

    def _load(self):
        if not self.path.exists():return
        try:
            con=sqlite3.connect(self.path)
            try: rows=con.execute('SELECT key,data FROM cache').fetchall()
            finally:con.close()
            for key,raw in rows:
                data=json.loads(raw)
                if key in self.state['rankings']:self.state['rankings'][key]=data
                elif key.startswith('history:'):self.state['histories'][key[8:]]=data
                elif key=='health':self.state.update(data)
                elif key=='smart':self.state['smart']=data
        except (OSError,sqlite3.Error,ValueError,TypeError):pass

    def _save(self,key,data):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            con=sqlite3.connect(self.path,timeout=2)
            try:
                con.execute('PRAGMA journal_mode=WAL')
                con.execute('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,data TEXT NOT NULL)')
                con.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(key,json.dumps(data,ensure_ascii=False,allow_nan=False)))
                con.commit()
            finally:con.close()
        except (OSError,sqlite3.Error):pass

    def snapshot(self):
        with self.lock:return deepcopy(self.state)

    @staticmethod
    def members(snapshot):
        # Keep the original named trader even when he drops out of both lists.
        result={pid:dict(id=pid,name=name,portfolio_type='PUBLIC',selected=True)
                for pid,name in curated_traders.TRADERS}
        for kind in ['ROI','PNL']:
            for row in snapshot['rankings'][kind]['rows']:
                member=result.setdefault(row['id'],dict(row))
                member[kind.lower()+'_rank']=row['rank']
                member.update({k:row[k] for k in ['name','roi','pnl','portfolio_type']})
        smart=snapshot.get('smart',{})
        for row in smart.get('rows',[]):
            profile=smart.get('profiles',{}).get(row['id'],{})
            pid=profile.get('portfolio_id')
            if not profile.get('public') or not pid or profile.get('error'):continue
            member=result.setdefault(pid,dict(id=pid,name=row['name'],portfolio_type='PUBLIC'))
            member.update(smart_rank=row['rank'],smart_id=row['id'])
        return result

    def _smart_step(self,session,now,snap):
        prior=snap.get('smart',{})
        if now>=prior.get('retry_at',0):
            try:
                rows=[];total=0
                for page in range(1,6):
                    part,total=smart_money.rank_page(session,page)
                    rows.extend(part)
                    if len(rows)>=min(total,100):break
                    if self.stop.wait(REQUEST_GAP):return 'stopped'
                if len(rows)!=min(total,100) or len({r['id'] for r in rows})!=len(rows):
                    raise source.SourceError('聪明钱分页覆盖不足，保留上次榜单')
                ids={r['id'] for r in rows}
                state=dict(rows=[dict(r,rank=i) for i,r in enumerate(rows,1)],profiles={k:v for k,v in prior.get('profiles',{}).items() if k in ids},
                           updated=now,retry_at=now+3600,error='')
            except (requests.RequestException,source.SourceError,ValueError,TypeError,KeyError) as exc:
                # An independent public directory failure does not stop copy orders.
                state=dict(prior,retry_at=now+max(900,getattr(exc,'cooldown',0)),error=str(exc) if isinstance(exc,source.SourceError) else '聪明钱目录查询失败')
            with self.lock:self.state['smart']=state
            self._save('smart',state)
            return 'smart:rank'
        if now<prior.get('profile_blocked_until',0):return None
        eligible=[r for r in prior.get('rows',[]) if now>=prior.get('profiles',{}).get(r['id'],{}).get('retry_at',0)]
        if not eligible:return None
        row=eligible[0];state=deepcopy(prior)
        try:
            profile=dict(smart_money.profile(session,row),checked=now,retry_at=now+3600,error='')
        except (requests.RequestException,source.SourceError,ValueError,TypeError,KeyError) as exc:
            profile=dict(prior.get('profiles',{}).get(row['id'],{}),checked=now,retry_at=now+900,error='公开资料暂不可用')
            if getattr(exc,'cooldown',0):state['profile_blocked_until']=now+exc.cooldown
        state.setdefault('profiles',{})[row['id']]=profile
        with self.lock:self.state['smart']=state
        self._save('smart',state)
        return 'smart:profile'

    def _failure(self,exc,now,prior):
        count=prior.get('failures',0)+1
        cooldown=getattr(exc,'cooldown',0)
        delay=max(min(1800,60*2**min(count-1,5)),cooldown)
        if cooldown:
            with self.lock:self.state.update(blocked_until=now+cooldown,error=str(exc))
            self._save('health',dict(blocked_until=now+cooldown,error=str(exc)))
        return dict(prior,attempted=now,retry_at=now+delay,failures=count,
                    error=str(exc) if isinstance(exc,source.SourceError) else '查询失败，保留缓存')

    def step(self,session,now=None):
        now=time.time() if now is None else now
        snap=self.snapshot()
        if now<snap['blocked_until']:return 'blocked'
        # New full rankings are published atomically; never present page 1 as top 50.
        for kind in ['ROI','PNL']:
            prior=snap['rankings'][kind]
            if now<prior.get('retry_at',0):continue
            try:
                first,total=rank_page(session,kind,1)
                second=[]
                if total>len(first):
                    if self.stop.wait(REQUEST_GAP):return 'stopped'
                    second,_=rank_page(session,kind,2)
                rows=combine_pages(first,second,total)
                state=dict(rows=rows,updated=now,retry_at=now+RANK_INTERVAL,error='',failures=0)
            except (requests.RequestException,source.SourceError,ValueError,TypeError,KeyError) as exc:
                state=self._failure(exc,now,prior)
            with self.lock:self.state['rankings'][kind]=state
            self._save(kind,state)
            return 'rank:'+kind
        # Share one serial request budget; reserve alternating turns for directory
        # checks so profile discovery cannot starve public order polling.
        members=self.members(snap)
        # Directory/profile refresh must not push already monitored operations
        # beyond the UI's five-minute freshness window after a restart.
        operations_due=any(member['portfolio_type']!='PRIVATE'
            and snap['histories'].get(pid,{}).get('updated')
            and now-snap['histories'][pid]['updated']>=240
            and now>=snap['histories'][pid].get('retry_at',0)
            for pid,member in members.items())
        if not getattr(self,'_smart_last',False) and not operations_due:
            self._smart_last=True
            result=self._smart_step(session,now,snap)
            if result:return result
        self._smart_last=False
        eligible=[]
        for pid,member in members.items():
            if member['portfolio_type']=='PRIVATE':continue
            history=snap['histories'].get(pid,{})
            if now>=history.get('retry_at',0):eligible.append(pid)
        if not eligible:return 'idle'
        # The original single-trader view retains its 30-second target.
        pid=source.PORTFOLIO if source.PORTFOLIO in eligible else min(eligible,key=lambda x:snap['histories'].get(x,{}).get('attempted',0))
        prior=snap['histories'].get(pid,dict(rows=[],updated=0))
        try:
            rows=source.fetch(session,now,portfolio_id=pid)
            state=dict(rows=rows,updated=now,attempted=now,
                       retry_at=now+(30 if pid==source.PORTFOLIO else ORDER_INTERVAL),error='',failures=0)
            with self.lock:self.state.update(error='',blocked_until=0)
        except (requests.RequestException,source.SourceError,ValueError,TypeError) as exc:
            state=self._failure(exc,now,prior)
        with self.lock:self.state['histories'][pid]=state
        self._save('history:'+pid,state)
        return 'history:'+pid

    def run(self):
        # Retire the original single-trader thread without overlapping requests.
        for thread in threading.enumerate():
            if thread.name==source.THREAD_NAME and hasattr(thread,'copy_monitor'):
                thread.copy_monitor.stop.set()
                thread.join(timeout=20)
        with requests.Session() as session:
            session.headers.update({'Accept':'application/json','User-Agent':'PerpWorkbench-Public-Lead-Viewer/1.0'})
            while not self.stop.is_set():
                result=self.step(session)
                wait=max(REQUEST_GAP,self.snapshot()['blocked_until']-time.time()) if result=='blocked' else 3 if result=='idle' else REQUEST_GAP
                self.stop.wait(wait)


def get_monitor():
    with _START_LOCK:
        for thread in threading.enumerate():
            if thread.name==THREAD_NAME and hasattr(thread,'copy_feed'):
                if getattr(thread,'copy_version',1)!=COLLECTOR_VERSION:
                    # Let its current bounded request finish before replacing
                    # this reader. UI keeps the cache and never waits for I/O.
                    thread.copy_feed.stop.set()
                return thread.copy_feed
        monitor=Monitor()
        thread=threading.Thread(target=monitor.run,name=THREAD_NAME,daemon=True)
        thread.copy_feed=monitor
        thread.copy_version=COLLECTOR_VERSION
        thread.start()
        return monitor


def ui_snapshot(snapshot,now=None):
    now=time.time() if now is None else now
    members=Monitor.members(snapshot)
    histories=snapshot['histories']
    events=[];fresh=0;unavailable=0;empty=0
    for pid,trader in members.items():
        state=histories.get(pid,{})
        if trader['portfolio_type']=='PRIVATE':unavailable+=1;continue
        if state.get('updated') and not state.get('error') and 0<=now-state['updated']<300:
            fresh+=1
            if not state.get('rows'):empty+=1
        for row in state.get('rows',[]):
            if not 0<=now-row['time']/1000<=7*86400:continue
            key=f'{pid}:{row["symbol"]}:{row["direction"]}:{row["order_time"]}'
            events.append(dict(row,id=key,trader_id=pid,name=trader['name'],
                               selected=trader.get('selected',False),smart_rank=trader.get('smart_rank'),smart_id=trader.get('smart_id'),
                               roi_rank=trader.get('roi_rank'),pnl_rank=trader.get('pnl_rank'),
                               observed=state.get('updated'),delayed=bool(state.get('error')) or now-state.get('updated',0)>=300))
    events.sort(key=lambda r:r['time'],reverse=True)
    # Bound browser work, while preserving the original trader's 50-row view.
    selected={r['id']:r for r in events[:500]}
    selected.update({r['id']:r for r in events if r.get('selected') or r.get('smart_rank')})
    smart=snapshot.get('smart',{});directory=[];trackable=[]
    for row in smart.get('rows',[]):
        profile=smart.get('profiles',{}).get(row['id'],{})
        pid=profile.get('portfolio_id');history=histories.get(pid,{})
        # A profile link alone is not proof of readable opening records.
        verified=bool(profile.get('public') and not profile.get('error') and history.get('updated') and not history.get('error') and any(r['direction'].startswith('开') for r in history.get('rows',[])))
        status='公开操作已接入' if verified else profile.get('error') or profile.get('reason') or '核验中'
        if not verified and pid and profile.get('public') and not profile.get('error'):
            if history.get('error'):status='关联跟单操作暂不可读 · 重试中'
            elif history.get('updated'):status='近7日窗口暂无公开开仓'
        item=dict(row,status=status,verified=verified)
        directory.append(item)
        if verified and len(trackable)<50:trackable.append(item)
    accepted={r['id'] for r in trackable}
    # Never label unverified/over-quota profiles as monitored smart-money members.
    for row in selected.values():
        if row.get('smart_id') not in accepted:row.update(smart_rank=None,smart_id=None)
    return dict(rankings=snapshot['rankings'],items=sorted(selected.values(),key=lambda r:r['time'],reverse=True),
                curated=[dict(id=pid,name=name,status='已采集' if histories.get(pid,{}).get('updated') and not histories.get(pid,{}).get('error') else '待采集 / 重试') for pid,name in curated_traders.TRADERS],
                smart=dict(rows=directory,trackable=trackable,updated=smart.get('updated',0),error=smart.get('error','')),
                traders=len(members),fresh=fresh,private=unavailable,empty=empty,
                pending=sum(1 for pid,m in members.items() if m['portfolio_type']!='PRIVATE' and not histories.get(pid,{}).get('attempted')),
                errors=sum(1 for pid in members if histories.get(pid,{}).get('error')),
                blocked_until=snapshot['blocked_until'],error=snapshot['error'],favorite=source.PORTFOLIO)
