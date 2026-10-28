import time
import pytest
import store
import monitoring
from live_collector import Monitor
from market_scanner import stock_contracts, monitored_contracts


def stock(symbol='TSLAUSDT', **changes):
    return dict(symbol=symbol, contractType='TRADIFI_PERPETUAL', status='TRADING',
                quoteAsset='USDT', marginAsset='USDT', underlyingType='EQUITY',
                underlyingSubType=['TradFi']) | changes


def test_stock_universe_covers_regions_without_commodities_or_other_currencies():
    info={'symbols':[
        stock(), stock('HKUSDT', underlyingType='HK_EQUITY'),
        stock('KRUSDT', underlyingType='KR_EQUITY'), stock('CNUSDT', underlyingType='CN_EQUITY'),
        stock('PREUSDT', underlyingType='PREMARKET'), stock('GOLDUSDT', underlyingType='COMMODITY'),
        stock('INDEXUSDT', underlyingType='INDEX'), stock('FXUSDT', underlyingType='FX'),
        stock('TSLAUSD1', quoteAsset='USD1', marginAsset='USD1'),
        stock('OLDUSDT', status='SETTLING'), stock('QUARTERUSDT', contractType='CURRENT_QUARTER'),
        stock('MISMATCHUSDT', marginAsset='USDC'),
        stock('ALTUSDT', contractType='PERPETUAL', underlyingType='COIN', underlyingSubType=['Crypto'])]}
    assert stock_contracts(info)==['CNUSDT','HKUSDT','KRUSDT','PREUSDT','TSLAUSDT']
    assert monitored_contracts(info)==['ALTUSDT','CNUSDT','GOLDUSDT','HKUSDT','KRUSDT','PREUSDT','TSLAUSDT']


@pytest.fixture
def stock_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path/'stocks.sqlite')
    store.put('settings',{'include_stocks':True})
    now=int(time.time()*1000)-1000
    info={'symbols':[stock(), stock('BTCUSDT', contractType='PERPETUAL',
                                  underlyingType='COIN', underlyingSubType=['Crypto'])]}
    class Client:
        def get(self, endpoint):
            if endpoint.endswith('exchangeInfo'): return info
            assert endpoint.endswith('ticker/24hr')
            return [{'symbol':'TSLAUSDT','lastPrice':'110','priceChangePercent':'-20',
                     'quoteVolume':'5000000','closeTime':now}]
    monitor=Monitor()
    monitor.client=Client()
    monitor.universe()
    monitor.quotes()
    return monitor, now


def test_stock_enters_stream_universe_and_same_four_hour_price_oi_rankings(stock_monitor):
    monitor, now=stock_monitor
    assert 'TSLAUSDT' in monitor.valid and 'TSLAUSDT' in monitor.market_valid
    assert monitoring.market_symbols()==['TSLAUSDT']  # BTC exclusion still applies.
    current=now//60000*60000
    with store.connect() as con:
        con.executemany('INSERT INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)',
            [('TSLAUSDT',t,100.,100.,100.,100.,1000.,int(t<current),now/1000)
             for t in range(current-498*60000,current+1,60000)])
    store.save_oi('TSLAUSDT',[(now-4*3600000,100,'live'),(now,120,'live')])
    rows=monitoring.build_rows(now=now/1000, scope='market')
    assert rows[0]['asset_type']=='stock'
    assert rows[0]['price_change']==pytest.approx(10)
    assert rows[0]['change24h']==-20
    assert rows[0]['oi_change']==pytest.approx(20)
    assert monitoring.ranked(rows,'up')[0]['symbol']=='TSLAUSDT'
    assert monitoring.ranked(rows,'oi_up')[0]['symbol']=='TSLAUSDT'


@pytest.mark.parametrize(('price','direction'), [(110,'急涨'),(90,'急跌')])
def test_stock_uses_existing_fifteen_minute_alarm(stock_monitor, price, direction):
    monitor, now=stock_monitor
    monitor.spike_prices['TSLAUSDT'].append((now-60000,100))
    monitor.inspect_spike('TSLAUSDT',price,now)
    assert [(r[2],r[3]) for r in store.spike_alerts()]==[('TSLAUSDT',direction)]


def test_closed_session_old_stock_ticks_do_not_rank_or_raise_alarm(stock_monitor):
    monitor, now=stock_monitor
    old=now-2*3600000
    store.put('live_market',{'tickers':{'TSLAUSDT':{'price':120,'event_time':old}}})
    monitor.spike_prices['TSLAUSDT'].append((old-60000,100))
    monitor.inspect_spike('TSLAUSDT',120,old)
    rows=monitoring.build_rows(now=now/1000,scope='market')
    assert rows[0]['price_fresh'] is False and rows[0]['price_change'] is None
    assert monitoring.ranked(rows,'up')==[] and store.spike_alerts()==[]


def test_stock_block_stops_new_alarms_hides_history_and_preserves_saved_focus(stock_monitor):
    monitor,now=stock_monitor
    store.put('settings',{'include_stocks':False,'top_symbols':['BTCUSDT','TSLAUSDT']})
    monitor.spike_config_time=0  # Apply a setting change just as the running stream does.
    monitor.spike_prices['TSLAUSDT'].append((now-60000,100))
    monitor.inspect_spike('TSLAUSDT',140,now)
    assert store.spike_alerts()==[]
    assert monitoring.market_symbols()==[]  # This fixture contains only stocks and excluded BTC.
    assert monitoring.build_rows(now=now/1000,scope='market')==[]
    reference={'price':100,'time':now-60000,'basis':'实时价格'}
    store.set_spike_episode('TSLAUSDT','急涨',True,now,40,reference,140)
    store.set_spike_episode('ALTUSDT','急涨',True,now,40,reference,140)
    records=store.spike_alerts(unacknowledged=True)
    assert [r[2] for r in monitoring.monitored_spike_alerts(records)]==['ALTUSDT']
    from monitor_ui import quick_prices,alarm_card
    assert 'TSLA' not in quick_prices(now/1000)
    assert 'TSLA' not in alarm_card(records,store.settings())
    assert store.settings()['top_symbols']==['BTCUSDT','TSLAUSDT']
    assert len(store.spike_alerts())==2
    store.put('settings',store.settings()|{'include_stocks':True})
    assert monitoring.market_symbols()==['TSLAUSDT']
    assert len(monitoring.monitored_spike_alerts(records))==2


def test_stock_block_does_not_silence_crypto_alarms(stock_monitor):
    monitor,now=stock_monitor
    store.put('settings',{'include_stocks':False})
    monitor.spike_config_time=0
    monitor.spike_prices['ALTUSDT'].append((now-60000,100))
    monitor.inspect_spike('ALTUSDT',110,now)
    assert [r[2] for r in store.spike_alerts()]==['ALTUSDT']


def test_commodity_and_etf_are_enabled_for_main_rankings_and_emergency_alarms(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'all-assets.sqlite')
    now=int(time.time()*1000)-1000
    info={'symbols':[stock('XAUUSDT',underlyingType='COMMODITY'),stock('SOXLUSDT')]}
    class Client:
        def get(self,path):
            if path.endswith('exchangeInfo'):return info
            return [dict(symbol=s,lastPrice='110',priceChangePercent='10',quoteVolume='100000',closeTime=now)
                    for s in ['XAUUSDT','SOXLUSDT']]
    monitor=Monitor();monitor.client=Client();monitor.universe();monitor.quotes()
    assert set(monitoring.market_symbols())=={'XAUUSDT','SOXLUSDT'}
    assert store.get('exchange_info')['commodity_symbols']==['XAUUSDT']
    for symbol in ['XAUUSDT','SOXLUSDT']:
        monitor.spike_prices[symbol].append((now-60000,100))
        monitor.inspect_spike(symbol,110,now)
    assert {r[2] for r in store.spike_alerts()}=={'XAUUSDT','SOXLUSDT'}
    rows=monitoring.build_rows(now=now/1000,scope='market')
    assert {r['symbol']:r['asset_type'] for r in rows}=={'XAUUSDT':'commodity','SOXLUSDT':'stock'}
