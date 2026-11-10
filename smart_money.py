"""Anonymous public directory only; never use authenticated position endpoints.

Smart Money IDs and copy portfolio IDs are distinct. A link is accepted only
when the public profile explicitly supplies futuresCopyTradePortfolioId.
"""
import math
import requests
import copy_trader as source

BASE = 'https://www.binance.com'
RANK_URL = BASE+'/bapi/futures/v1/friendly/future/smart-money/top-trader/list'
PROFILE_URL = BASE+'/bapi/asset/v1/friendly/future/smart-money/profile'


def public_get(session,url,params):
    response=session.get(url,params=params,timeout=(5,12),allow_redirects=False)
    if response.status_code in (401,403,418,429):
        try: retry=float(response.headers.get('Retry-After',0))
        except (TypeError,ValueError):retry=0
        raise source.SourceError('聪明钱来源访问受限，暂停查询',max(900,retry if math.isfinite(retry) else 900))
    if response.status_code!=200:raise source.SourceError('聪明钱公开来源暂不可用')
    value=response.json()
    if not isinstance(value,dict) or value.get('code')!='000000' or value.get('success') is False:
        raise source.SourceError('聪明钱来源未提供公开资料')
    return value


def rank_page(session,page):
    value=public_get(session,RANK_URL,dict(page=page,rows=20,timeRange='1Y',
        rankingType='PNL',order='DESC',onlyShowSharingPosition=True))
    data=value['data'];rows=[]
    if not isinstance(data,dict) or not isinstance(data.get('rows'),list):raise source.SourceError('聪明钱榜单格式异常')
    for raw in data['rows']:
        if not isinstance(raw,dict):raise source.SourceError('聪明钱榜单行格式异常')
        pid=str(raw.get('topTraderId',''))
        if not pid.isdigit() or not 10<=len(pid)<=25:raise source.SourceError('聪明钱榜单缺少标识')
        pnl,roi=float(raw['pnl']),float(raw['roi'])*100
        if not math.isfinite(pnl) or not math.isfinite(roi):raise source.SourceError('聪明钱收益格式异常')
        rows.append(dict(id=pid,name=str(raw.get('traderName') or pid).strip()[:120],
                         pnl=pnl,roi=roi,position_status=raw.get('positionStatus','UNKNOWN')))
    return rows,int(data.get('total',len(rows)))


def profile(session,trader):
    value=public_get(session,PROFILE_URL,dict(topTraderId=trader['id']))
    data=value.get('data')
    if not isinstance(data,dict) or str(data.get('topTraderId'))!=trader['id']:
        raise source.SourceError('聪明钱资料标识不一致')
    public=data.get('enable') is True and data.get('sharingPosition') is True and data.get('sharingLatestRecord') is True
    pid=data.get('futuresCopyTradePortfolioId')
    pid=str(pid) if pid else ''
    if pid and (not pid.isdigit() or not 10<=len(pid)<=25):raise source.SourceError('关联跟单项目格式异常')
    return dict(public=public,portfolio_id=pid if public else '',
                reason='公开跟单项目待核验' if public and pid else '最新操作需登录授权' if public else '私密仓位或操作 · 不采集')
