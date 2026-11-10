"""Read-only public lead operations. Network work never runs in the UI fragment."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
import html
import json
from pathlib import Path
import re
import threading
import time

import requests

PORTFOLIO = '5075281354358777856'
NAME = '熬鹰资本'
PAGE = f'https://www.binance.com/zh-CN/copy-trading/lead-details/{PORTFOLIO}'
ENDPOINT = 'https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history'
INTERVAL = 30
ZONE = timezone(timedelta(hours=8))
CACHE = Path(__file__).resolve().parent / 'user_data' / 'copy_trader.json'
THREAD_NAME = 'workbench-public-copy-trader'
MONITOR_VERSION = 2
_START_LOCK = threading.Lock()


def operation_rows(items):
    """Map hedge position side and order side to opening/closing operations.

    orderTime is stable while partial-fill updates change update time/average.
    The source exposes no order ID, so this is a display, not an execution ledger.
    """
    if not isinstance(items, list):
        raise ValueError('操作列表格式异常')
    rows = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('操作记录格式异常')
        direction = {('BUY', 'LONG'): '开多', ('SELL', 'SHORT'): '开空',
                     ('SELL', 'LONG'): '平多', ('BUY', 'SHORT'): '平空'}.get(
            (item.get('side'), item.get('positionSide')))
        if not direction:
            continue
        symbol = item.get('symbol', '')
        try:
            stamp = int(item['orderUpdateTime'])
            order_time = int(item.get('orderTime') or stamp)
            price = Decimal(str(item['avgPrice']))
            quantity = Decimal(str(item['executedQty']))
            if (not re.fullmatch(r'[A-Z0-9_]{2,40}', symbol)
                    or not 946684800000 <= order_time <= stamp < 4102444800000
                    or not price.is_finite() or not quantity.is_finite()
                    or price <= 0 or quantity <= 0):
                raise ValueError('操作记录缺少有效时间或成交价格')
        except (KeyError, TypeError, InvalidOperation, OverflowError) as exc:
            raise ValueError('操作记录缺少有效时间或成交价格') from exc
        text = format(price, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        with localcontext() as context:
            context.prec = 60
            notional = quantity * price
            notional_display = format(notional.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), ',.2f')
        quote_asset = str(item.get('quoteAsset') or 'USDT')
        if not re.fullmatch(r'[A-Z0-9]{2,12}', quote_asset):
            raise ValueError('操作记录缺少有效计价币种')
        row = dict(symbol=symbol, direction=direction, time=stamp,
                   order_time=order_time, price=text, quantity=format(quantity,'f'),
                   notional=format(notional,'f'),notional_display=notional_display,quote_asset=quote_asset)
        key = (symbol, direction, order_time)
        if key not in rows or stamp >= rows[key]['time']:
            rows[key] = row
    return sorted(rows.values(), key=lambda row: row['time'], reverse=True)[:50]


class SourceError(Exception):
    def __init__(self, message, cooldown=0):
        super().__init__(message)
        self.cooldown = cooldown


def fetch(session, now, portfolio_id=PORTFOLIO):
    response = session.post(ENDPOINT, json=dict(
        portfolioId=portfolio_id, startTime=int((now - 7 * 86400) * 1000),
        endTime=int(now * 1000), pageSize=50), timeout=(5, 12), allow_redirects=False)
    if response.status_code in (401, 403, 418, 429):
        try:
            retry = max(0, float(response.headers.get('Retry-After', 0)))
        except (ValueError, TypeError):
            retry = 0
        raise SourceError('币安访问限制，稍后重试', max(900, retry))
    if response.status_code != 200:
        raise SourceError(f'币安接口暂不可用（{response.status_code}）')
    payload = response.json()
    if not isinstance(payload, dict) or payload.get('code') != '000000' or payload.get('success') is False:
        raise SourceError('币安未返回成功结果')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('list'), list):
        raise SourceError('币安操作列表格式异常')
    return operation_rows(data['list'])


class Monitor:
    def __init__(self, path=CACHE):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state = dict(portfolio_id=PORTFOLIO, rows=[], updated=0,
                          attempted=0, retry_at=0, error='')
        self.failures = 0
        try:
            cached = json.loads(self.path.read_text(encoding='utf-8'))
            if cached.get('portfolio_id') == PORTFOLIO and isinstance(cached.get('rows'), list):
                self.state.update({k: cached[k] for k in self.state if k in cached})
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def poll(self, session, now=None):
        supplied_time = now is not None
        now = time.time() if now is None else now
        if now < self.snapshot()['retry_at']:
            return
        try:
            rows = fetch(session, now)
            self.failures = 0
            change = dict(rows=rows, updated=now if supplied_time else time.time(),
                          attempted=now, retry_at=now + INTERVAL, error='')
        except (requests.RequestException, ValueError, SourceError) as exc:
            self.failures += 1
            delay = max(min(900, INTERVAL * 2 ** min(self.failures, 5)),
                        getattr(exc, 'cooldown', 0))
            message = str(exc) if isinstance(exc, SourceError) else '查询失败，保留上次记录'
            change = dict(attempted=now, retry_at=now + delay, error=message)
        with self.lock:
            self.state.update(change)
            saved = deepcopy(self.state)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.path)
        except OSError:
            # A full/locked disk must not kill the thread or the live display.
            pass

    def run(self):
        with requests.Session() as session:
            session.headers.update({'Accept': 'application/json',
                                    'User-Agent': 'PerpWorkbench-Public-Lead-Viewer/1.0'})
            while not self.stop.is_set():
                self.poll(session)
                wait = max(1, self.snapshot()['retry_at'] - time.time())
                self.stop.wait(wait)


def get_monitor():
    # Reuse the process-owned thread across sessions and Streamlit hot reloads.
    # Only this thread performs requests; the three-second UI reads memory.
    with _START_LOCK:
        for thread in threading.enumerate():
            if thread.name == THREAD_NAME and hasattr(thread, 'copy_monitor'):
                if getattr(thread, 'copy_version', 1) != MONITOR_VERSION:
                    # Retire only our old reader. The next UI tick starts the
                    # new version after its bounded request completes; no join
                    # or network wait is allowed in the UI fragment.
                    thread.copy_monitor.stop.set()
                return thread.copy_monitor
        monitor = Monitor()
        thread = threading.Thread(target=monitor.run, name=THREAD_NAME, daemon=True)
        thread.copy_monitor = monitor
        thread.copy_version = MONITOR_VERSION
        thread.start()
        return monitor


def card(snapshot, now=None):
    now = time.time() if now is None else now
    esc = lambda value: html.escape(str(value), quote=True)
    updated = snapshot.get('updated', 0)
    stale = not updated or not 0 <= now - updated < 90 or snapshot.get('error')
    status = '等待首次获取' if not updated else ('数据延迟 · ' if stale else '更新 ') + datetime.fromtimestamp(updated, ZONE).strftime('%H:%M:%S')
    if snapshot.get('error'):
        status += ' · ' + snapshot['error']
    rows = []
    for row in snapshot.get('rows', [])[:50]:
        stamp = datetime.fromtimestamp(row['time'] / 1000, ZONE)
        css = {'开多': 'up-accent', '开空': 'down-accent'}.get(row['direction'], 'oi-accent')
        key = f'{row["symbol"]}:{row["direction"]}:{row["order_time"]}'
        rows.append(f'<tr data-entry-key="{esc(key)}"><td><span>{esc(row["symbol"])}</span> <small class="{css}">{esc(row["direction"])}</small></td>'
                    f'<td><time datetime="{stamp.isoformat(timespec="seconds")}">{stamp:%m-%d %H:%M:%S}</time></td>'
                    f'<td><b>{esc(row["price"])}</b> <small>USDT</small></td></tr>')
    content = ('<table><thead><tr><th>标的 / 操作</th><th>操作时间 · 北京时间</th><th>成交均价</th></tr></thead><tbody>'
               + ''.join(rows) + '</tbody></table>') if rows else '<div class="copy-empty">' + ('近7日最新50条操作中暂无开平仓成交记录' if updated else '正在获取币安公开操作记录…') + '</div>'
    return (f'<section class="copy-trader-card" data-signal-kind="copy" aria-label="熬鹰资本操作动态"><div class="copy-title">'
            f'<a href="{PAGE}" target="_blank" rel="noopener noreferrer">熬鹰资本 · 操作动态</a>'
            f'<span class="{"offline" if stale else "muted"}">{esc(status)}</span></div>'
            '<div class="copy-scroll" role="region" aria-label="开平仓记录，手动滚动" tabindex="0">' + content + '</div>'
            '<div class="copy-note">每30秒查询 · 开平仓含加减仓 · 时间为成交更新时刻 · 币安披露可能延迟</div></section>')
