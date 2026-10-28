import time
import pytest
import store
import live_collector
from live_collector import Monitor


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'simple.sqlite')


def test_sampling_does_not_fetch_orderbook(isolated):
    m=Monitor()
    calls=[]
    now=time.time()
    m.seeded['XUSDT']=now
    m.ws_kline_last['XUSDT']=now
    class Client:
        blocked_until=0
        def get(self,path,**params):
            calls.append(path)
            if path=='/fapi/v1/openInterest': return {'time':int(now*1000),'openInterest':'200'}
            if path=='/futures/data/openInterestHist': return []
            pytest.fail('Unexpected data collection: '+path)
    m.local.client=Client()
    assert m.sample('XUSDT')==[]
    assert '/fapi/v1/openInterest' in calls
    assert '/fapi/v1/depth' not in calls


def test_main_cycle_only_creates_spike_alarms_even_if_legacy_options_enabled(isolated,monkeypatch):
    store.put('settings',store.DEFAULTS|{'watchlist':['XUSDT'],'quiet_mode':False})
    store.put('ai_config',{'auto':True,'profiles':[]})
    m=Monitor()
    stamp=int(time.time()*1000)-1000
    m.valid={'XUSDT'}
    m.alt_valid={'XUSDT'}
    m.market_valid={'XUSDT'}
    m.spike_prices['XUSDT'].append((stamp-60000,100.))
    m.ticks={'XUSDT':{'price':110.,'event_time':stamp}}
    m.universe=lambda:None
    m.quotes=lambda:None
    m.sample=lambda s:[]
    threads=[]
    class Thread:
        def __init__(self,target,**kwargs): threads.append(target.__qualname__)
        def start(self): pass
    monkeypatch.setattr(live_collector.threading,'Thread',Thread)
    # Remove the need for an executor's internal threads in this one-symbol cycle.
    class Future:
        def result(self): return []
    class Pool:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def submit(self,*args): return Future()
    monkeypatch.setattr(live_collector,'ThreadPoolExecutor',Pool)
    monkeypatch.setattr(live_collector,'as_completed',lambda fs:list(fs))
    def no_legacy(*args,**kwargs): pytest.fail('Legacy composite evaluation was run')
    monkeypatch.setattr(live_collector.monitoring,'build_rows',no_legacy)
    class EndCycle(Exception): pass
    def finish(*args): raise EndCycle()
    monkeypatch.setattr(live_collector.time,'sleep',finish)
    with pytest.raises(EndCycle): m.run()
    assert len(store.spike_alerts())==1
    assert not store.recent_alerts()
    assert all('worker' not in name for name in threads)
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM radar_events').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0]==0
