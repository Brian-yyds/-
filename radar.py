"""Evidence-first radar. Scores describe conditions, never a prediction probability."""
import json
import math
import statistics
import hashlib
import time
from collections import deque
import store

VERSION = "radar-1.0"


class FlowTracker:
    """Bounded per-second buckets; reset coverage on reconnect or aggTrade ID gaps."""
    def __init__(self):
        self.buckets = deque()
        self.started = None
        self.last_id = None
        self.last_time = None
        self.gaps = 0

    def add(self, trade):
        ident, stamp = int(trade['a']), int(trade['T'])
        price, quantity = float(trade['p']), float(trade['q'])
        if not all(math.isfinite(x) and x > 0 for x in (price, quantity)):
            return
        if self.last_id is not None and ident <= self.last_id:
            return
        if self.last_id is not None and (ident != self.last_id + 1 or stamp < self.last_time):
            self.buckets.clear()
            self.started = None
            self.gaps += 1
        self.last_id, self.last_time = ident, stamp
        if self.started is None:
            self.started = stamp
        second = stamp // 1000 * 1000
        quote = price * quantity
        # m=true: buyer is maker, hence the aggressor is a seller.
        buy = not trade['m']
        if not self.buckets or self.buckets[-1][0] != second:
            self.buckets.append([second, 0., 0., 0., 0., price, price])
        bucket = self.buckets[-1]
        bucket[1 if buy else 2] += quote
        bucket[3 if buy else 4] = max(bucket[3 if buy else 4], quote)
        bucket[6] = price
        while self.buckets and self.buckets[0][0] < stamp - 360000:
            self.buckets.popleft()

    def metrics(self, now_ms):
        recent = [b for b in self.buckets if now_ms-300000 <= b[0] <= now_ms]
        buy, sell = sum(b[1] for b in recent), sum(b[2] for b in recent)
        total = buy + sell
        span = max(0, (now_ms - self.started)/1000) if self.started is not None else 0
        fresh = self.last_time is not None and 0 <= now_ms-self.last_time <= 20000
        return {'buy5': buy, 'sell5': sell, 'delta5': buy-sell,
                'imbalance5': (buy-sell)/total*100 if total else None,
                'max_buy5': max((b[3] for b in recent), default=0),
                'max_sell5': max((b[4] for b in recent), default=0),
                'change5': (recent[-1][6]/recent[0][5]-1)*100 if recent else None,
                'ready': bool(fresh and span >= 300), 'fresh': fresh,
                'coverage_seconds': min(300, span), 'last_time': self.last_time,
                'gaps': self.gaps, 'computed_at': now_ms/1000}


def sweep(levels, quote, mid, buy):
    remaining, base = quote, 0.
    for price, qty in levels:
        used = min(remaining, price*qty)
        base += used/price
        remaining -= used
        if remaining <= quote*1e-10:
            vwap = quote/base
            return {'complete': True, 'slippage_bps': (vwap/mid-1)*10000*(1 if buy else -1), 'vwap': vwap}
    return {'complete': False, 'slippage_bps': None, 'vwap': None}


def depth_metrics(raw, band_bps=10., quote=10000., now=None):
    now = time.time() if now is None else now
    bids = sorted([(float(p), float(q)) for p,q in raw['bids'] if float(q)>0], reverse=True)
    asks = sorted([(float(p), float(q)) for p,q in raw['asks'] if float(q)>0])
    if not bids or not asks or bids[0][0] >= asks[0][0] or any(not math.isfinite(v) or v<=0 for levels in (bids,asks) for r in levels for v in r):
        raise ValueError('盘口为空、交叉或数值无效')
    mid = (bids[0][0]+asks[0][0])/2
    low, high = mid*(1-band_bps/10000), mid*(1+band_bps/10000)
    bid = sum(p*q for p,q in bids if p>=low)
    ask = sum(p*q for p,q in asks if p<=high)
    covered = bids[-1][0]<=low and asks[-1][0]>=high
    stamp = int(raw.get('E', raw.get('T', 0)))
    return {'mid':mid, 'spread_bps':(asks[0][0]-bids[0][0])/mid*10000,
            'bid_quote':bid, 'ask_quote':ask, 'band_bps':band_bps, 'band_complete':covered,
            'imbalance':(bid-ask)/(bid+ask)*100 if bid+ask and covered else None,
            'buy_sweep':sweep(asks,quote,mid,True), 'sell_sweep':sweep(bids,quote,mid,False),
            'sweep_quote':quote, 'event_time':stamp, 'sampled_at':now,
            'fresh':0 <= now*1000-stamp <= 15000, 'bid_drop_pct':None,
            'baseline_samples':0, 'source':'REST /depth 500档快照'}


def save_depth(symbol, metric):
    now = metric['sampled_at']
    with store.connect() as con:
        previous = [json.loads(r[0]) for r in con.execute(
            'SELECT data FROM depth_samples WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t',
            (symbol,now-300,now-30))]
        valid = [p for p in previous if p.get('fresh') and p.get('band_complete')
                 and p['band_bps']==metric['band_bps'] and abs(p['mid']/metric['mid']-1)<.01]
        if metric['fresh'] and metric['band_complete'] and len(valid)>=4 and valid[-1]['sampled_at']-valid[0]['sampled_at']>=90:
            baseline = statistics.median(p['bid_quote'] for p in valid)
            metric['bid_drop_pct'] = (1-metric['bid_quote']/baseline)*100 if baseline>0 else None
            metric['baseline_samples'] = len(valid)
        con.execute('INSERT OR REPLACE INTO depth_samples VALUES (?,?,?)',
                    (symbol,now,json.dumps(metric,allow_nan=False)))


def inputs(symbols, now=None):
    now = time.time() if now is None else now
    flows = store.get('order_flow', {})
    books = {}
    with store.connect() as con:
        for symbol in symbols:
            r = con.execute('SELECT data FROM depth_samples WHERE symbol=? ORDER BY t DESC LIMIT 1',(symbol,)).fetchone()
            if r:
                books[symbol] = json.loads(r[0])
    for f in flows.values():
        f['ready'] = f.get('ready',False) and 0<=now-f.get('computed_at',0)<=15 and 0<=now*1000-(f.get('last_time') or 0)<=20000
    for b in books.values():
        b['fresh'] = b.get('fresh',False) and 0<=now-b['sampled_at']<=65 and 0<=now*1000-b['event_time']<=65000
    return flows,books


def hour_flow(symbol, now_ms):
    start = now_ms//60000*60000-59*60000
    with store.connect() as con:
        rows = con.execute('SELECT m.t,m.q,m.seen,t.buy FROM minute_candles m JOIN taker_minutes t ON m.symbol=t.symbol AND m.t=t.t WHERE m.symbol=? AND m.t>=? AND m.t<=? ORDER BY m.t',
                           (symbol,start,now_ms//60000*60000)).fetchall()
    if len(rows)!=60 or now_ms/1000-rows[-1][2]>25:
        return {'buy1h':None,'sell1h':None,'delta1h':None}
    total,buy = sum(r[1] for r in rows),sum(r[3] for r in rows)
    return {'buy1h':buy,'sell1h':max(0,total-buy),'delta1h':2*buy-total}


def assess(row, flow, book, cfg):
    evidence, missing, observations = [], [], []
    signal = None
    if not row['price_fresh']: missing.append('价格过期')
    if row['price_change'] is None or row['quote_ratio'] is None: missing.append('1小时行情不完整')
    if not flow.get('ready'): missing.append('主动成交连续覆盖不足5分钟或已过期')
    if not book.get('fresh'): missing.append('盘口快照缺失或已过期')
    elif book.get('band_bps')!=cfg['depth_band_bps'] or book.get('sweep_quote')!=cfg['sweep_quote']:
        missing.append('盘口参数已修改，等待按新参数采样')
    elif not book.get('band_complete'): observations.append('500档未覆盖整个价格带：带内深度仅为下界，不能用其判断失衡或抽走流动性')
    if row['oi_basis']!='实时采样': observations.append('OI为历史基准或积累中：不作为确认增仓的证据')
    if missing:
        return {'kind':None,'evidence':[], 'missing':missing,'observations':observations,'level':'数据待齐'}
    change, ratio = row['price_change'],row['quote_ratio']
    imbalance = flow.get('imbalance5')
    rule = cfg | cfg.get('overrides',{}).get(row['symbol'],{})
    amount_ok = row['quote'] is not None and row['quote']>=max(cfg['radar_min_quote'],rule['min_quote'])
    scope_ok = not cfg['radar_priority_only'] or row['priority']
    enabled = cfg.get('overrides',{}).get(row['symbol'],{}).get('enabled',True)
    if not amount_ok: observations.append('1小时成交额低于雷达门槛：只展示，不发常规事件')
    if not scope_ok: observations.append('当前仅重点币允许常规事件')
    volume = ratio>=cfg['radar_volume_ratio']
    spread_ok = book['spread_bps']<=cfg['radar_max_spread_bps']
    oi_up = row['oi_basis']=='实时采样' and row['oi_change'] is not None and row['oi_change']>=cfg['radar_oi_pct']
    buying = imbalance is not None and imbalance>=cfg['radar_flow_pct']
    selling = imbalance is not None and imbalance<=-cfg['radar_flow_pct']
    liquid = book.get('band_complete') and book['bid_quote']>0 and book['ask_quote']>0
    bid_drop = book.get('bid_drop_pct') is not None and book['bid_drop_pct']>=cfg['radar_depth_drop_pct']
    slip = book['sell_sweep'].get('slippage_bps')
    weak_bid = bid_drop or (slip is not None and slip>=cfg['radar_slip_bps']) or (liquid and book['imbalance']<=-20)
    if abs(change)<=cfg['radar_quiet_pct'] and volume and oi_up and buying and spread_ok:
        signal = '蓄势候选'
        evidence = ['价格仍在安静区间','1小时成交额放大','实时OI数量增加','5分钟主动买入占优']
        observations.append('候选仅供观察；需后续价格突破与订单流延续，不能据此断言即将启动')
    if change>=rule['up_pct'] and volume and buying and spread_ok and liquid:
        signal = '上行异动确认'
        evidence = ['1小时价格达到上涨阈值','1小时成交额放大','5分钟主动买入占优','价格带盘口覆盖完整且价差达标']
    falling = change<=-rule['down_pct'] or (flow.get('change5') is not None and flow['change5']<=-cfg['radar_accel_pct'])
    if falling and selling and (volume or flow['max_sell5']>=cfg['radar_large_quote']) and weak_bid:
        signal = '抛压异动确认'
        evidence = ['1小时跌幅或5分钟加速下跌达标','5分钟主动卖出占优','放量或大额聚合卖出','买盘深度下降、失衡或模拟卖出滑点偏高']
    if not signal and selling and falling:
        observations.append('价格与卖流同向，但成交/盘口证据未齐：保留观察，不升级报警')
    eligible = amount_ok and scope_ok and enabled
    return {'kind':signal,'evidence':evidence,'missing':missing,'observations':observations,
            'eligible':eligible,'level':'安静观察' if signal=='蓄势候选' else '待持续确认' if signal and eligible else '仅展示' if signal else '暂无共振'}


def snapshot(row, flow, book, decision, now=None, cfg=None):
    cfg=store.settings() if cfg is None else cfg
    effective=cfg | cfg.get('overrides',{}).get(row['symbol'],{})
    rules={k:v for k,v in effective.items() if k.startswith('radar_') or k in ('up_pct','down_pct','min_quote','enabled','confirm_seconds','release_seconds','cooldown_minutes','depth_band_bps','sweep_quote')}
    rule_id=hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest()[:12]
    return {'version':VERSION,'rule_id':rule_id,'rules_at_capture':rules,'captured_at':time.time() if now is None else now,'market':'Binance USDT perpetual',
            'regular_window_minutes':60,'flow_confirmation_minutes':5,'row':row,'flow':flow,
            'flow_1h':hour_flow(row['symbol'],int((time.time() if now is None else now)*1000)), 'book':book,'decision':decision,
            'limitations':['OI不能区分多空方向','盘口是30秒采样的可见挂单，不是完整撤单追踪，也不包含隐藏流动性',
                          '聚合成交不是单个大户订单；5分钟delta是主动买减主动卖的成交额，不是资金净流入',
                          '候选与共振条件未经胜率验证，不能保证提前发现启动或捕获每次砸盘']}


def advance(symbol, kind, condition, data, cfg, now=None):
    """One event per episode. Persist debounce, cooldown and restart latch."""
    now = time.time() if now is None else now
    created = None
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if data.get('rule_id'):
            key='radar_rule:'+symbol
            previous=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            if not previous or json.loads(previous[0])!=data['rule_id']:
                # New rules must earn their own confirmation time. Keep active
                # episode latches so an edit cannot spam an ongoing event.
                con.execute('UPDATE radar_state SET since=NULL WHERE symbol=? AND active=0',(symbol,))
                con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,json.dumps(data['rule_id'])))
        r = con.execute('SELECT since,last_seen,last_true,active,last_sent FROM radar_state WHERE symbol=? AND kind=?',(symbol,kind)).fetchone()
        since,last_seen,last_true,active,last_sent = r or (None,0,0,0,0)
        if condition:
            if since is None or (not active and now-last_seen>45): since = now
            last_true = now
            if not active and now-since>=cfg['confirm_seconds'] and now-last_sent>=cfg['cooldown_minutes']*60:
                cur = con.execute('INSERT INTO radar_events(time,symbol,kind,data) VALUES (?,?,?,?)',
                                  (now,symbol,kind,json.dumps(data,ensure_ascii=False,allow_nan=False)))
                created,active,last_sent = cur.lastrowid,1,now
        else:
            since = None
            if now-last_true>=cfg['release_seconds']: active=0
        con.execute('INSERT OR REPLACE INTO radar_state VALUES (?,?,?,?,?,?,?)',(symbol,kind,since,now,last_true,int(active),last_sent))
        con.execute('DELETE FROM radar_events WHERE id NOT IN (SELECT id FROM radar_events ORDER BY id DESC LIMIT 5000)')
    return created


def events(limit=100):
    with store.connect() as con:
        return [dict(id=i,time=t,symbol=s,kind=k,snapshot=json.loads(d)) for i,t,s,k,d in con.execute(
            'SELECT id,time,symbol,kind,data FROM radar_events ORDER BY id DESC LIMIT ?',(limit,))]


def local_analysis(data):
    r,f,b,d = data['row'],data['flow'],data['book'],data['decision']
    lines = [f"**{r['symbol']} · {d.get('kind') or d['level']}**",'这是本机证据解读，不是外部模型生成。']
    if r.get('price_change') is not None:
        lines.append(f"1小时价格 {r['price_change']:+.2f}%；成交额 {r['quote']:,.0f} USDT。")
    if f.get('ready'):
        lines.append(f"过去5分钟主动买入 {f['buy5']:,.0f} / 主动卖出 {f['sell5']:,.0f} USDT，成交差 {f['delta5']:+,.0f} USDT，方向强度 {f['imbalance5']:+.1f}%。")
        short=f.get('change5')
        if short is not None and f.get('imbalance5') is not None:
            direction=f['imbalance5']
            if direction>=15 and short>.15:
                lines.append('**可能解释**：短线价格与主动买入同向，存在买方推动；需要继续看卖盘补单和放量是否持续，单凭成交差不能判断买方身份。')
            elif direction<=-15 and short<-.15:
                lines.append('**可能解释**：短线价格与主动卖出同向，卖方正在推动价格；若买盘深度同步下降或卖出滑点抬高，抛压风险更值得关注。')
            elif direction>=15 and short<=.15:
                lines.append('**可能解释**：主动买入较强但价格未明显跟涨，可能有被动卖盘吸收买单；这也可能是上涨受阻，不能直接解释为吸筹。观察价格能否抬升并站稳。')
            elif direction<=-15 and short>=-.15:
                lines.append('**可能解释**：主动卖出较强但价格没有明显跟跌，可能有被动买盘承接，也可能只是短暂平衡。观察承接深度是否保持、后续卖压能否打穿。')
            else:
                lines.append('**可能解释**：主动买卖暂未形成明显单边优势，不急于给出方向判断。')
    if r.get('oi_change') is not None:
        lines.append(f"1小时OI数量 {r['oi_change']:+.2f}%（{r['oi_basis']}）。"+('价格与OI同时变化可能涉及新开仓与平仓的混合，不能据此识别净多或净空。' if r['oi_basis']=='实时采样' else '历史基准仅作背景，不用于确认增仓事件。'))
    if b.get('fresh'):
        coverage = '完整' if b.get('band_complete') else '不完整，深度仅为下界'
        lines.append(f"可见盘口价差 {b['spread_bps']:.2f} bps；±{b['band_bps']:g} bps价格带覆盖{coverage}。买/卖深度 {b['bid_quote']:,.0f} / {b['ask_quote']:,.0f} USDT。")
        if b.get('bid_drop_pct') is not None:
            lines.append(f"买盘带内深度相对前1–5分钟可比快照中位数变化 {-b['bid_drop_pct']:+.1f}%；这不等于已确认撤单。")
        for side,key in [('买入','buy_sweep'),('卖出','sell_sweep')]:
            v=b[key]['slippage_bps']
            lines.append(f"模拟{b['sweep_quote']:,.0f} USDT{side}："+(f"相对中间价滑点 {v:.2f} bps（未含手续费）" if v is not None else '当前500档不足以完整成交，无法给出完整滑点')+'。')
    for label,key in [('触发证据','evidence'),('缺失项','missing'),('观察限制','observations')]:
        if d.get(key): lines.append(f"**{label}**："+'；'.join(d[key])+'。')
    lines.append('**后续观察**：买卖方向是否延续、放量是否持续、买盘深度是否恢复。价格方向与主动成交背离时，先降低判断强度；OI增加只能说明未平仓数量增加。')
    return '\n\n'.join(lines)
