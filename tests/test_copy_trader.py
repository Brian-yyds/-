from pathlib import Path
import sys
from types import SimpleNamespace
from decimal import Decimal

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import copy_trader as c

NOW = 1794298800
STAMP = 1794243318634


def item(**kwargs):
    return dict(symbol='NVDAUSDT', side='BUY', positionSide='LONG',
                avgPrice='177.13000', executedQty=564.58, orderTime=STAMP-2000,
                orderUpdateTime=STAMP, **kwargs)


def session(items=None, status=200, payload=None):
    data = payload if payload is not None else dict(code='000000', success=True, data=dict(list=items or []))
    return SimpleNamespace(post=lambda *a, **k: SimpleNamespace(status_code=status, headers={}, json=lambda: data))


def test_all_four_directions_and_partial_fill_updates():
    original = item()
    update = dict(original, avgPrice='177.1400', orderUpdateTime=STAMP+500)
    short = dict(original, symbol='BTCUSDT', side='SELL', positionSide='SHORT')
    close_long = dict(original, side='SELL')
    close_short = dict(short, side='BUY')
    unknown = dict(original, positionSide='BOTH')
    rows = c.operation_rows([original, update, short, close_long, close_short, unknown])
    assert len(rows) == 4
    assert rows[0]['price'] == '177.14'
    assert {r['direction'] for r in rows} == {'开多', '开空', '平多', '平空'}
    assert next(r for r in rows if r['direction']=='平多')['price']=='177.13'
    assert Decimal(rows[0]['notional'])==Decimal('100009.7012')
    assert rows[0]['notional_display']=='100,009.70'
    assert next(r for r in rows if r['direction']=='平多')['notional_display']=='100,004.06'


def test_operation_notional_uses_executed_quantity_decimal_and_quote_asset():
    row=c.operation_rows([dict(item(),avgPrice='0.1',executedQty='0.2',quoteAsset='USDC')])[0]
    assert row['notional']=='0.02' and row['notional_display']=='0.02'
    assert row['quantity']=='0.2' and row['quote_asset']=='USDC'
    row=c.operation_rows([dict(item(),avgPrice='1.005',executedQty='1')])[0]
    assert row['notional_display']=='1.01'


@pytest.mark.parametrize('change', [dict(avgPrice='NaN'), dict(avgPrice='0'),
    dict(executedQty='Infinity'), dict(orderUpdateTime=0), dict(symbol='<script>')])
def test_invalid_success_not_accepted_as_fresh(change):
    with pytest.raises(ValueError):
        c.operation_rows([dict(item(), **change)])


def test_failure_preserves_rows_and_respects_retry_after_restart(tmp_path):
    path = tmp_path / 'copy.json'
    monitor = c.Monitor(path)
    monitor.poll(session([item()]), NOW)
    good = monitor.snapshot()
    monitor.poll(session(status=429), NOW+30)
    failed = monitor.snapshot()
    assert failed['rows'] == good['rows'] and failed['updated'] == NOW
    assert failed['retry_at'] >= NOW+930
    restarted = c.Monitor(path)
    restarted.poll(SimpleNamespace(post=lambda *a, **k: pytest.fail('cooldown ignored')), NOW+60)
    assert restarted.snapshot() == failed
    assert '数据延迟' in c.card(failed, NOW+31)


def test_bad_payload_keeps_last_good_and_empty_success_clears(tmp_path):
    monitor = c.Monitor(tmp_path / 'copy.json')
    monitor.poll(session([item()]), NOW)
    monitor.poll(session(payload=dict(code='000000', data=None)), NOW+30)
    assert len(monitor.snapshot()['rows']) == 1
    monitor.poll(session([]), NOW+100)
    assert monitor.snapshot()['rows'] == []
    assert '暂无开平仓' in c.card(monitor.snapshot(), NOW+100)


def test_network_error_isolated(tmp_path):
    def timeout(*args, **kwargs):
        raise requests.Timeout('no connection')
    monitor = c.Monitor(tmp_path / 'copy.json')
    monitor.poll(SimpleNamespace(post=timeout), NOW)
    assert monitor.snapshot()['error'] and not monitor.snapshot()['updated']


def test_card_shows_beijing_time_exact_price_and_escapes_status():
    markup = c.card(dict(rows=c.operation_rows([item(),dict(item(),side='SELL')]), updated=NOW, error='<unsafe>'), NOW)
    assert '11-10 00:55:18' in markup and '<b>177.13</b>' in markup
    assert 'NVDAUSDT' in markup and '开多' in markup
    assert '平多' in markup and '操作时间' in markup
    assert '&lt;unsafe&gt;' in markup and '<unsafe>' not in markup
    assert '数据延迟' in c.card(dict(rows=[], updated=NOW), NOW+90)


def test_request_is_public_bounded_and_read_only():
    calls = []
    fake = session([item()])
    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return fake.post(*args, **kwargs)
    assert c.fetch(SimpleNamespace(post=post), NOW)
    args, kwargs = calls[0]
    assert args == (c.ENDPOINT,)
    assert kwargs['json']['portfolioId'] == '5075281354358777856'
    assert kwargs['json']['pageSize'] == 50
    assert kwargs['json']['endTime'] - kwargs['json']['startTime'] == 7*86400000
    assert kwargs['allow_redirects'] is False and 'headers' not in kwargs
