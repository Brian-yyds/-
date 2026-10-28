from datetime import datetime
import pytest
import store
import volume_monitor as v

NOW=datetime(2026,10,27,13,30,tzinfo=v.ZONE).timestamp()


def row(t,quote=1):
    return [t,'1','1','1','1','999999999',t+v.HOUR-1,str(quote)]


def contract(symbol,**fields):
    return dict(symbol=symbol,contractType='PERPETUAL',status='TRADING',quoteAsset='USDT',
                marginAsset='USDT',underlyingType='COIN',underlyingSubType=['Crypto'],onboardDate=0)|fields


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'volume.sqlite')
    monkeypatch.setattr(v.time,'time',lambda:NOW)


def test_fifteen_beijing_days_includes_today():
    start=v.window_start(NOW)
    assert datetime.fromtimestamp(start/1000,v.ZONE).isoformat()=='2026-10-13T00:00:00+08:00'
    assert len(v.expected_hours(0,start,NOW))==14*24+14
    assert v.window_start(datetime(2026,10,28,0,tzinfo=v.ZONE).timestamp())==start+v.DAY


def test_uses_quote_turnover_including_open_hour_and_rejects_invalid_values():
    start=v.window_start(NOW)
    current=int(NOW*1000)//v.HOUR*v.HOUR
    assert v.parse_rows([row(current,123.5)],start,NOW)=={current:123.5}
    for quote in ['NaN',-1,'inf']:
        with pytest.raises(ValueError):v.parse_rows([row(current,quote)],start,NOW)
    with pytest.raises(ValueError):v.parse_rows([row(current),row(current)],start,NOW)


def test_missing_listed_hour_never_becomes_zero_market_volume():
    start=v.window_start(NOW)
    values={t:1 for t in v.expected_hours(0,start,NOW)}
    values.pop(start+v.HOUR)
    result=v.aggregate({'BTCUSDT':0},{'BTCUSDT':values},start,NOW)
    assert not result['complete'] and result['total'] is None
    assert result['missing']==['BTCUSDT']


def test_new_listing_does_not_need_history_before_its_onboard_date():
    start=v.window_start(NOW)
    onboard=int(NOW*1000)-10*60000
    first=onboard//v.HOUR*v.HOUR
    result=v.aggregate({'NEWUSDT':onboard},{'NEWUSDT':{first:25}},start,NOW)
    assert result['complete'] and result['total']==25
    assert [d['quote'] for d in result['days']]==[0]*14+[25]


class Client:
    def __init__(self): self.calls=[];self.multiplier=1;self.fail=False
    def get(self,path,**params):
        self.calls.append((path,params))
        if path.endswith('exchangeInfo'):
            return {'symbols':[contract('BTCUSDT'),contract('ALTUSDT'),
                    contract('TSLAUSDT',contractType='TRADIFI_PERPETUAL',underlyingType='EQUITY'),
                    contract('SOXLUSDT',contractType='TRADIFI_PERPETUAL',underlyingType='EQUITY'),
                    contract('XAUUSDT',contractType='TRADIFI_PERPETUAL',underlyingType='COMMODITY'),
                    contract('INDEXUSDT',underlyingType='INDEX'),contract('OLDUSDT',status='SETTLING')]}
        assert path=='/fapi/v1/klines' and params['interval']=='1h'
        if self.fail and params['symbol']=='ALTUSDT': raise RuntimeError('offline')
        start=params['startTime'];end=int(NOW*1000)//v.HOUR*v.HOUR
        quote=(2 if params['symbol']=='BTCUSDT' else 3)*self.multiplier
        return [row(t,quote) for t in range(start,end+1,v.HOUR)][:params['limit']]


def test_cycle_includes_btc_excludes_stocks_and_updates_cache_without_double_count(isolated):
    client=Client();monitor=v.Monitor();monitor.get=client.get
    assert monitor.cycle()==v.PAUSE
    snapshot=store.get(v.SNAPSHOT_KEY)
    hours=len(v.expected_hours(0,v.window_start(NOW),NOW))
    assert snapshot['complete'] and snapshot['symbol_count']==2
    assert snapshot['total']==hours*5
    assert {p['symbol'] for path,p in client.calls if path.endswith('klines')}=={'BTCUSDT','ALTUSDT'}
    client.calls=[];client.multiplier=2
    monitor.cycle()
    assert all(p['limit']<100 for path,p in client.calls if path.endswith('klines'))
    assert store.get(v.SNAPSHOT_KEY)['total']==snapshot['total']+10  # Only the last two cached hours change.
    previous=store.get(v.SNAPSHOT_KEY)
    client.fail=True;client.multiplier=10
    monitor.cycle()
    assert store.get(v.SNAPSHOT_KEY)==previous  # One failing contract cannot publish a partial market sum.
    assert store.get('market_volume_health')['failed']==1


def test_pair_has_fifteen_days_each_and_no_summary_block(isolated):
    from monitor_ui import market_volume_card
    store.put('market_volume_health',dict(status='正在汇总',completed=10,total=520))
    loading=market_volume_card(NOW)
    assert '10/520' in loading and 'volume-total' not in loading
    values={t:2 for t in v.expected_hours(0,v.window_start(NOW),NOW)}
    snapshot=v.aggregate({'BTCUSDT':0},{'BTCUSDT':values},v.window_start(NOW),NOW)
    store.put(v.SNAPSHOT_KEY,snapshot|dict(updated_at=NOW))
    store.put('market_volume_health',dict(status='统计完成'))
    ready=market_volume_card(NOW)
    assert ready.count('class="volume-day')==30
    assert ready.count('data-volume-kind=')==2
    assert 'volume-total' not in ready and 'volume-today' not in ready and 'volume-meta' not in ready
    assert '前10' in ready and '亿USDT' in ready
    assert 'data-market-volume="ready"' in ready
    assert 'data-market-volume="loading"' in market_volume_card(NOW+v.DAY)


def test_top_ten_are_selected_separately_each_day():
    start=v.window_start(NOW)
    symbols={f'COIN{i:02d}USDT':0 for i in range(12)}
    cache={s:{t:float(i+1) for t in v.expected_hours(0,start,NOW)} for i,s in enumerate(symbols)}
    for hour in range(24):
        cache['COIN00USDT'][start+v.DAY+hour*v.HOUR]=100
        cache['COIN01USDT'][start+v.DAY+hour*v.HOUR]=90
    result=v.aggregate(symbols,cache,start,NOW)
    first,second=result['days'][:2]
    assert first['quote']==24*sum(range(1,13))
    assert first['top10_quote']==24*sum(range(3,13))
    assert 'COIN00USDT' not in {t['symbol'] for t in first['top10']}
    assert second['top10_quote']==24*(100+90+sum(range(5,13)))
    assert second['top10'][0]['symbol']=='COIN00USDT'
    assert all(d['top10_quote']<=d['quote'] and len(d['top10'])==10 for d in result['days'])
