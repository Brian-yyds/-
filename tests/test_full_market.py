import time
import pytest
import store
import monitoring as m
from market_scanner import crypto_contracts


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'market.sqlite')


def contract(symbol,**changes):
    return dict(symbol=symbol,contractType='PERPETUAL',status='TRADING',quoteAsset='USDT',marginAsset='USDT',underlyingType='COIN',underlyingSubType=['Crypto'])|changes


def test_only_crypto_usdt_perpetuals_are_market_candidates():
    raw={'symbols':[contract('ALTUSDT'),contract('BTCUSDT'),contract('INDEXUSDT',underlyingType='INDEX'),
                    contract('STOCKUSDT',underlyingSubType=['Stock']),contract('OLDUSDT',status='SETTLING'),
                    contract('ALTUSDC',quoteAsset='USDC'),contract('QUARTER',contractType='CURRENT_QUARTER')]}
    assert crypto_contracts(raw)==['ALTUSDT','BTCUSDT']


def seed_market(now):
    current=now//60000*60000
    symbols=['BTCUSDT','FOCUSUSDT','PUMPUSDT','DUMPUSDT']
    store.put('exchange_info',{'symbols':symbols,'alt_symbols':symbols})
    store.put('live_market',{'tickers':{s:{'price':p,'event_time':now,'change24h':v} for s,p,v in
        [('BTCUSDT',100,1),('FOCUSUSDT',101,2),('PUMPUSDT',120,-5),('DUMPUSDT',80,30)]}})
    with store.connect() as con:
        for s in symbols:
            con.executemany('INSERT INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)',
                [(s,t,100.,100.,100.,100.,1000.,int(t<current),now/1000) for t in range(current-498*60000,current+1,60000)])
    return store.DEFAULTS|{'watchlist':['FOCUSUSDT']}


def test_full_market_ranking_includes_altcoins_outside_focus_pool(isolated):
    now=180_030_000
    cfg=seed_market(now)
    rows=m.build_rows(cfg,now/1000,scope='market')
    assert {r['symbol'] for r in rows}=={'FOCUSUSDT','PUMPUSDT','DUMPUSDT'}
    assert m.ranked(rows,'up')[0]['symbol']=='PUMPUSDT'
    assert m.ranked(rows,'down')[0]['symbol']=='DUMPUSDT'
    assert m.ranked(rows,'up')[0]['price_change']==pytest.approx(20)
    assert m.ranked(rows,'up')[0]['change24h']==-5
    assert len(m.build_rows(cfg,now/1000))==1


def test_four_hour_reference_is_not_the_239_minute_or_one_hour_open(isolated):
    now=180_030_000
    cfg=seed_market(now)
    current=now//60000*60000
    with store.connect() as con:
        con.execute('UPDATE minute_candles SET o=110 WHERE symbol=? AND t IN (?,?)',('PUMPUSDT',current-239*60000,current-60*60000))
    r=next(r for r in m.build_rows(cfg,now/1000,scope='market') if r['symbol']=='PUMPUSDT')
    assert r['price_change']==pytest.approx(20)
    assert r['base_price']==100 and r['price_span']==240.5
    assert r['quote']==240000  # Four hours of actual USDT volume, not one hour.


def test_live_price_reference_takes_precedence_without_future_leakage(isolated):
    now=180_030_000
    cfg=seed_market(now)
    target=now-4*3600000
    with store.connect() as con:
        con.executemany('INSERT INTO price_samples VALUES (?,?,?,?)',
            [('PUMPUSDT',(target-6000)//5000,target-6000,110.),('PUMPUSDT',(target+1000)//5000,target+1000,115.)])
    r=next(r for r in m.build_rows(cfg,now/1000,scope='market') if r['symbol']=='PUMPUSDT')
    assert r['base_price']==110 and r['base_time']==target-6000
    assert r['price_change']==pytest.approx((120/110-1)*100)
    assert '实时' in r['price_basis']


def test_new_coin_without_four_hour_history_is_not_assigned_zero(isolated):
    now=180_030_000
    cfg=seed_market(now)
    with store.connect() as con:
        con.execute('DELETE FROM minute_candles WHERE symbol=? AND t<?',('PUMPUSDT',now-1800000))
    rows=m.build_rows(cfg,now/1000,scope='market')
    coin=next(r for r in rows if r['symbol']=='PUMPUSDT')
    assert coin['price_change'] is None and coin['change24h']==-5
    assert all(r['symbol']!='PUMPUSDT' for r in m.ranked(rows,'up'))


def test_all_market_oi_sampling_age_does_not_relax_focus_age(isolated):
    now=180_030_000
    cfg=seed_market(now)
    for symbol in ['PUMPUSDT','FOCUSUSDT']:
        store.save_oi(symbol,[(now-14500000,100,'live'),(now-100000,110,'live')])
    rows=m.build_rows(cfg,now/1000,scope='market')
    assert next(r for r in rows if r['symbol']=='PUMPUSDT')['oi_change']==pytest.approx(10)
    assert next(r for r in rows if r['symbol']=='FOCUSUSDT')['oi_change'] is None


def test_oi_ranking_read_preserves_late_current_and_historical_baseline_tolerance(isolated):
    now=180030000
    latest=now-180000
    store.save_oi('ALTUSDT',[(latest,120,'live'),
        (latest-4*3600000-330000,100,'history5m'),(now-2*3600000,50,'live'),(now+1000,999,'live')])
    complete,_=store.live_inputs(['ALTUSDT'],now/1000)
    selected,_=store.live_inputs(['ALTUSDT'],now/1000,oi_window_minutes=240)
    expected=m.oi_metrics(complete['ALTUSDT'],now,240,max_age_ms=180000)
    actual=m.oi_metrics(selected['ALTUSDT'],now,240,max_age_ms=180000)
    assert actual==expected
    assert actual['oi_change']==pytest.approx(20)
    assert actual['oi_basis']=='5m历史快照'
