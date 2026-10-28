import pytest
import store
import monitoring


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'focus.sqlite')


def test_focus_including_excluded_btc_uses_exact_four_hours_and_rejects_future_sample(isolated):
    now=180030000
    target=now-4*3600000
    store.put('live_market',{'tickers':{'BTCUSDT':{'price':120.,'event_time':now}}})
    with store.connect() as con:
        con.executemany('INSERT INTO price_samples VALUES (?,?,?,?)',[
            ('BTCUSDT',(target-5000)//5000,target-5000,100.),
            ('BTCUSDT',(target+1000)//5000,target+1000,110.)])
    quote=monitoring.focus_quotes(['BTCUSDT'],now/1000)['BTCUSDT']
    assert quote['change']==pytest.approx(20)
    assert quote['basis']=='实时价格采样'
    assert quote['fresh']
    assert monitoring.focus_quotes(['BTCUSDT'],now/1000+30)['BTCUSDT']['change'] is None


def test_focus_fallback_uses_four_hour_minute_open_never_1h_or_24h_change(isolated):
    now=180030000
    target=(now-4*3600000)//60000*60000
    store.put('live_market',{'tickers':{'ETHUSDT':{'price':90.,'event_time':now,'change24h':99}}})
    with store.connect() as con:
        con.execute('INSERT INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)',('ETHUSDT',target,100.,101.,99.,101.,1000.,1,now/1000))
        con.execute('INSERT INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)',('ETHUSDT',target+3*3600000,75.,75.,75.,75.,1000.,1,now/1000))
    assert monitoring.focus_quotes(['ETHUSDT'],now/1000)['ETHUSDT']['change']==pytest.approx(-10)
    assert monitoring.focus_quotes(['NEWUSDT'],now/1000)['NEWUSDT']['change'] is None


def test_top_symbols_are_priorities_and_receive_focus_sampling(isolated):
    store.put('settings',dict(top_symbols=['TSLAUSDT'],priority=['SOLUSDT'],watchlist=['ETHUSDT']))
    cfg=store.settings()
    assert cfg['priority']==['SOLUSDT','TSLAUSDT']
    assert cfg['watchlist']==['TSLAUSDT','ETHUSDT']


def test_retention_keeps_four_hour_baselines_and_both_volume_windows(isolated,monkeypatch):
    now=1_800_000_000_000
    monkeypatch.setattr(store.time,'time',lambda:now/1000)
    baseline=now-4*3600000
    store.put('live_market',{'tickers':{'BTCUSDT':{'price':120.,'event_time':now}}})
    with store.connect() as con:
        con.executemany('INSERT INTO price_samples VALUES (?,?,?,?)',[
            ('BTCUSDT',baseline//5000,baseline,100.),
            ('BTCUSDT',(now-5*3600000)//5000,now-5*3600000,90.)])
        con.executemany('INSERT INTO minute_candles VALUES (?,?,?,?,?,?,?,?,?)',[
            ('BTCUSDT',now-hours*3600000,100.,100.,100.,100.,1000.,1,now/1000) for hours in (4,8,10)])
    store.save_oi('BTCUSDT',[(baseline,100,'live'),(now,110,'live')])
    store.prune_live()
    quote=monitoring.focus_quotes(['BTCUSDT'],now/1000)['BTCUSDT']
    assert quote['change']==pytest.approx(20)
    oi,bars=store.live_inputs(['BTCUSDT'],now/1000)
    assert monitoring.oi_metrics(oi['BTCUSDT'],now,240)['oi_change']==pytest.approx(10)
    assert {b[0] for b in bars['BTCUSDT']}=={now-4*3600000,now-8*3600000}
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM price_samples').fetchone()[0]==1
