import pytest
import store
import monitoring
import price_history as history

NOW=1793232000.


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'history.sqlite')


def candle(t,price=100):return [t,str(price),'105','95','101','4',t+59999,'400']


def test_exact_72_hour_minute_and_batch_cache_survive_restart(isolated):
    seen=[];target=int(NOW*1000)-history.WINDOW
    def get(path,weight,**kw):
        seen.append(kw)
        return [candle(target+i*60000,100+i) for i in range(history.BATCH)]
    history.Monitor(get).sample('TESTUSDT',NOW)
    history.Monitor(get).sample('TESTUSDT',NOW+60)
    assert len(seen)==1 and seen[0]['startTime']==target and seen[0]['limit']==90
    assert history.baselines({'TESTUSDT':int((NOW+60)*1000)+1234})=={'TESTUSDT':(target+60000,101)}
    assert history.baselines({'TESTUSDT':int((NOW+90*60)*1000)})=={}


def test_missing_new_listing_closed_session_and_invalid_values_are_not_substituted(isolated):
    target=int(NOW*1000)-history.WINDOW
    seen=[]
    def get(*args,**kw):seen.append(kw);return [candle(target+60000)]
    m=history.Monitor(get);m.sample('NEWUSDT',NOW);m.sample('NEWUSDT',NOW+60)
    assert len(seen)==1 and history.baselines({'NEWUSDT':int(NOW*1000)})=={}
    assert history.baselines({'NEWUSDT':int((NOW+60)*1000)})['NEWUSDT'][1]==100
    history.Monitor(lambda *a,**k:[candle(target,float('nan'))]).sample('BADUSDT',NOW)
    assert history.baselines({'BADUSDT':int(NOW*1000)})=={}


def test_returns_use_real_72_hour_baseline_and_leave_four_hour_sort_unchanged(isolated):
    target=int(NOW*1000)-history.WINDOW
    history.Monitor(lambda *a,**k:[candle(target,50)]).sample('TESTUSDT',NOW)
    store.put('exchange_info',dict(market_symbols=['TESTUSDT'],symbols=['TESTUSDT']))
    store.put('live_market',dict(tickers={'TESTUSDT':dict(price=100,event_time=int(NOW*1000))}))
    with store.connect() as con:
        base=int(NOW*1000)-240*60000
        con.execute('INSERT INTO price_samples VALUES (?,?,?,?)',('TESTUSDT',base//5000,base,80))
    row=monitoring.build_rows(now=NOW,scope='market')[0]
    assert row['price_change_3d']==100 and row['price_change']==25
    assert row['base_3d_time']==target
    assert monitoring.ranked([row],'up')==[row]
    stale=monitoring.build_rows(now=NOW+100,scope='market')[0]
    assert stale['price_change_3d'] is None
