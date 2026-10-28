"""Read-only GMGN scans, isolated from the Binance collector and browser refreshes."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import store
import gmgn_equities as equities
import gmgn_tracking as tracking
from gmgn_credentials import api_key

CHAINS = ('sol', 'bsc', 'base', 'robinhood')
CHAIN_LABELS = {'sol':'SOL','bsc':'BSC','base':'BASE','robinhood':'ROB'}
ZONE = timezone(timedelta(hours=8))
DEFAULTS = dict(enabled=True, graduate_volume=200000., other_volume=1000000., people=3,kol_count=2,exclude_equities=True,
                poll_seconds=15,tracking_days=10,daily_limit=1)


def configuration():
    return DEFAULTS | store.get('gmgn_config', {}) | {'exclude_equities':True,'tracking_days':10,'daily_limit':1}


def number(value):
    if isinstance(value,bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def valid_address(chain, address):
    pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain == 'sol' else r'0x[0-9a-fA-F]{40}'
    return chain in CHAINS and isinstance(address, str) and re.fullmatch(pattern, address) is not None


def token_url(token):
    if not valid_address(token.get('chain'), token.get('address')):
        return ''
    return 'https://gmgn.ai/' + token['chain'] + '/token/' + quote(token['address'], safe='')


def normalize(raw, chain, stamp):
    if not isinstance(raw, dict) or not valid_address(chain, raw.get('address')):
        return None
    # Keep unknowns unknown: missing volume/counts must never become a trigger.
    fields = ('volume', 'market_cap', 'holder_count', 'smart_degen_count', 'renowned_count',
              'open_timestamp', 'creation_timestamp', 'visiting_count')
    token = {k: number(raw.get(k)) for k in fields}
    token.update({k: str(raw.get(k) or '')[:300] for k in
                  ('address', 'symbol', 'name', 'logo', 'launchpad', 'launchpad_platform', 'launchpad_status')})
    if raw.get('launchpad_status') is not None:
        token['launchpad_status']=str(raw['launchpad_status'])
    token.update(chain=chain, observed=stamp)
    token['launch_known'] = 'launchpad' in raw and 'launchpad_platform' in raw
    evidence = equities.equity_reason(token)
    token.update(asset_class='equity' if evidence else 'unclassified', equity_evidence=evidence)
    return token


def launch_kind(token):
    # `pool_*` is a DEX pool label, not evidence of a launchpad. A retained
    # launchpad ID still takes priority after migration to a pool_* platform.
    if token.get('launch_known') is False:
        return 'unknown'
    launchpad = token.get('launchpad', '').strip()
    platform = token.get('launchpad_platform', '').strip()
    if launchpad and not launchpad.startswith('pool_'):
        return 'launchpad'
    if platform and not platform.startswith('pool_'):
        return 'launchpad'
    if 'launchpad' in token and 'launchpad_platform' in token:
        return 'direct'
    return 'unknown'


def reason(token, cfg, now, followup=False):
    if equities.equity_reason(token):
        return ''
    kol=token.get('renowned_count')
    if kol is None or kol<=cfg.get('kol_count',2):
        return ''
    volume = token.get('volume')
    if volume is None:
        return ''
    kind = launch_kind(token)
    if kind == 'direct':
        if str(token.get('launchpad_status'))=='0':
            return ''
        return '非发射台放量' if volume > cfg['other_volume'] else ''
    if kind != 'launchpad' or str(token.get('launchpad_status')) not in ('1', '2'):
        return ''
    opened = token.get('open_timestamp')
    # Open market time after migration; never substitute token creation time.
    midnight = datetime.fromtimestamp(now, ZONE).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if not followup and (opened is None or not midnight <= opened <= now):
        return ''
    smart, kol = token.get('smart_degen_count'), token.get('renowned_count')
    if smart is None or kol is None:
        return ''
    return ('毕业代币再次放量' if followup else '今日毕业放量') if volume > cfg['graduate_volume'] and smart + kol > cfg['people'] else ''


def schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS gmgn_alerts (id INTEGER PRIMARY KEY, chain TEXT, address TEXT, rule TEXT, triggered REAL, last_seen REAL, acknowledged INTEGER DEFAULT 0, data TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS gmgn_episodes (chain TEXT, address TEXT, rule TEXT, active INTEGER, last_true REAL, below_since REAL, observed REAL, PRIMARY KEY(chain,address,rule))')
    tracking.schema(con)


def record_scan(chain, tokens, cfg, stamp):
    return tracking.record_scan(chain,tokens,cfg,stamp)


def pending_alerts():
    with store.connect() as con:
        schema(con)
        rows = con.execute('SELECT id,rule,triggered,last_seen,data FROM gmgn_alerts WHERE acknowledged=0 ORDER BY triggered DESC,id DESC').fetchall()
    seen, result = set(), []
    for ident, rule, triggered, last_seen, data in rows:
        token = json.loads(data)
        if equities.equity_reason(token):
            continue
        key = (token['chain'],token['address'])
        if key not in seen:
            result.append(dict(id=ident,rule=rule,triggered=triggered,last_seen=last_seen,token=token))
            seen.add(key)
    return result


def recent_alerts(limit=3):
    with store.connect() as con:
        schema(con)
        rows=con.execute('SELECT id,rule,triggered,last_seen,acknowledged,data FROM gmgn_alerts ORDER BY triggered DESC,id DESC').fetchall()
    result=[];days=set()
    for i,rule,triggered,seen,ack,data in rows:
        token=json.loads(data)
        key=(*tracking.identity(token['chain'],token['address']),tracking.day(triggered))
        if equities.equity_reason(token) or key in days:continue
        days.add(key)
        result.append(dict(id=i,rule=rule,triggered=triggered,last_seen=seen,acknowledged=bool(ack),token=token))
    return result[:max(0,min(int(limit),3))]


def acknowledge(ids):
    # Acknowledge only records actually presented when the button was clicked.
    with store.connect() as con:
        schema(con)
        con.executemany('UPDATE gmgn_alerts SET acknowledged=1 WHERE id=?', [(i,) for i in ids])


def pending_ids():
    with store.connect() as con:
        schema(con)
        return [ident for ident,data in con.execute('SELECT id,data FROM gmgn_alerts WHERE acknowledged=0')
                if not equities.equity_reason(json.loads(data))]


def system_proxy():
    if os.name != 'nt':
        return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if not winreg.QueryValueEx(key,'ProxyEnable')[0]:
                return ''
            value = winreg.QueryValueEx(key,'ProxyServer')[0]
        if '=' in value:
            entries = dict(part.split('=',1) for part in value.split(';') if '=' in part)
            value = entries.get('https',entries.get('http',''))
        return ('http://' + value) if value and '://' not in value else value
    except (OSError, ValueError):
        return ''


class QueryError(Exception):
    def __init__(self, message, delay=30):
        super().__init__(message)
        self.delay = delay


class Client:
    def __init__(self):
        self.last_call = 0

    def execute(self, args):
        cooldown=store.get('gmgn_rate_limit_until',0)
        if cooldown>time.time():
            raise QueryError('GMGN 限频，已暂停请求等待恢复',cooldown-time.time())
        node = shutil.which('node')
        executable = shutil.which('gmgn-cli') or shutil.which('gmgn-cli.cmd')
        if not node or not executable:
            raise QueryError('请先安装 Node.js 和 gmgn-cli（npm install -g gmgn-cli@1.6.9）', 300)
        candidate = Path(executable).parent / 'node_modules/gmgn-cli/dist/index.js'
        script = candidate if candidate.exists() else Path(executable).resolve()
        try:
            key = api_key()
        except (ValueError, OSError):
            raise QueryError('本机 GMGN 密钥暂不可读取，请使用保存密钥的 Windows 账户启动', 300)
        if not key:
            raise QueryError('等待配置 GMGN API Key', 60)
        env = os.environ.copy()
        env['GMGN_API_KEY'] = key
        env['GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS'] = '0'
        env.pop('GMGN_PRIVATE_KEY', None)
        env.pop('GMGN_DEBUG', None)
        if not any(env.get(k) for k in ('HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy')):
            proxy = system_proxy()
            if proxy:
                env['HTTPS_PROXY'] = proxy
        time.sleep(max(0, 1.1 - (time.monotonic() - self.last_call)))
        request = {'command':args[1]}
        if args[1]=='trending':
            request['chain']=args[args.index('--chain')+1]
        elif args[:2]==['token','info-batch']:
            params=json.loads(args[args.index('--params')+1])
            if not isinstance(params,list) or not 1<=len(params)<=10 or any(not isinstance(p,dict) or not valid_address(p.get('chain'),p.get('address')) for p in params):
                raise ValueError('只允许最多10个已验证地址的只读代币查询')
            request['tokens']=[dict(chain=p['chain'],address=p['address']) for p in params]
        try:
            proc = subprocess.run([node,str(store.ROOT/'gmgn_bridge.mjs'),str(script)], cwd=store.ROOT, env=env,
                                  input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',timeout=80 if args[1]=='info-batch' else 25,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except (subprocess.TimeoutExpired, OSError):
            raise QueryError('GMGN 连接超时，稍后重试', 30)
        finally:
            self.last_call = time.monotonic()
        try:
            envelope=json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise QueryError('GMGN 返回格式异常，请检查官方工具安装', 60)
        if not envelope.get('ok'):
            error=envelope.get('error',{})
            if error.get('status')==429 or 'RATE_LIMIT' in error.get('code',''):
                delay=max(5,float(error.get('reset_at',0))-time.time()+2) if error.get('reset_at') else 305
                store.put('gmgn_rate_limit_until',time.time()+delay)
                raise QueryError('GMGN 限频，已暂停请求等待恢复', delay)
            if error.get('status') in (401,403):
                raise QueryError('GMGN 授权或套餐限制，请检查 API Key 权限', 300)
            raise QueryError('GMGN 数据暂不可用，稍后重试', 30)
        data=envelope.get('data')
        if isinstance(data,dict) and 'code' in data:
            if data['code']!=0:
                raise QueryError('GMGN 返回数据错误，稍后重试')
            return data.get('data')
        return data

    def query(self, args):
        # No trade commands or arbitrary endpoints are accepted.
        if tuple(args[:2]) not in (('market','trending'),('token','info-batch')):
            raise ValueError('仅允许 GMGN 只读行情查询')
        return self.execute(args)


class Monitor:
    def __init__(self, client=None):
        self.refresh_equities = client is None
        self.client = client or Client()

    def cycle(self):
        cfg=configuration()
        snapshot=store.get('gmgn_market',{})
        for obsolete in ('hot','hot_leaders','hot_scope','hot_updated','volume_leader','leader_filter'):
            snapshot.pop(obsolete,None)
        snapshot.setdefault('chains',{})
        if not cfg['enabled']:
            snapshot.update(heartbeat=time.time(),status='已暂停 GMGN 监控',enabled=False)
            store.put('gmgn_market',snapshot)
            return
        if self.refresh_equities:equities.refresh_catalogs()
        errors=[]
        for chain in CHAINS:
            try:
                result=self.client.query(['market','trending','--chain',chain,'--interval','5m',
                    '--order-by','volume','--direction','desc','--limit','100','--filter','is_internal_market','is_out_market','--raw'])
                if not isinstance(result,dict) or not isinstance(result.get('rank'),list):
                    raise QueryError('GMGN 候选数据格式变化，稍后重试')
                stamp=time.time()
                tokens=[t for raw in result['rank'] if (t:=normalize(raw,chain,stamp))]
                record_scan(chain,tokens,cfg,stamp)
                threshold=min(cfg['graduate_volume'],cfg['other_volume'])
                capped=len(tokens)>=100 and any(t['volume'] is None or t['volume']>=threshold for t in tokens[-1:])
                snapshot['chains'][chain]=dict(updated=stamp,count=len(tokens),capped=capped)
                snapshot.update(heartbeat=stamp,enabled=True,status='正在扫描四链')
                store.put('gmgn_market',snapshot)
            except QueryError as error:
                if store.get('gmgn_rate_limit_until',0)>time.time():raise
                errors.append(error)
        try:tracking.refresh_info(self.client,cfg)
        except QueryError as error:
            if store.get('gmgn_rate_limit_until',0)>time.time():raise
            errors.append(error)
        stamp=time.time()
        snapshot.update(heartbeat=stamp,enabled=True,status='监控中' if not errors else str(errors[0]),
                        retry_at=stamp+max(e.delay for e in errors) if errors else 0)
        snapshot['watch_ranking']=tracking.ranking_snapshot(stamp)
        if not errors:snapshot['last_success']=stamp
        store.put('gmgn_market',snapshot)
        if errors:raise errors[0]


def run():
    monitor = Monitor()
    while True:
        delay = max(15,configuration()['poll_seconds'])
        try:
            monitor.cycle()
        except QueryError as exc:
            delay = exc.delay
            snapshot = store.get('gmgn_market',{})
            snapshot.update(heartbeat=time.time(),status=str(exc),retry_at=time.time()+delay)
            store.put('gmgn_market',snapshot)
        except Exception:
            # Do not log provider payloads, subprocess environments, or credentials.
            delay = 30
            snapshot = store.get('gmgn_market',{})
            snapshot.update(heartbeat=time.time(),status='GMGN 采集暂不可用，稍后重试')
            store.put('gmgn_market',snapshot)
        # Wait after completion: request time must not eat the configured pause.
        # For errors, the delay/reset deadline is measured when the error arrives.
        time.sleep(max(1,delay))


if __name__ == '__main__':
    run()
