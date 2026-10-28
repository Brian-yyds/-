import json
import math
from datetime import timezone,timedelta
import pytest
import strategy_lab as lab

NOW=1800000000.
STEP=900000


def bars(count=140,flat=False):
    last=int(NOW*1000)//STEP*STEP-STEP
    result=[]
    for i in range(count):
        stamp=last-(count-1-i)*STEP
        close=100. if flat else 100.+i
        result.append(dict(time=stamp,close_time=stamp+STEP-1,open=close-.2,high=close+.3,low=close-.3,
                           close=close,volume=100.,quote_volume=1000.,closed=True))
    return result


def api_rows(items):
    return [[b['time'],str(b['open']),str(b['high']),str(b['low']),str(b['close']),str(b['volume']),b['close_time'],str(b['quote_volume']),0] for b in items]


def test_market_reads_only_public_candles_and_validates_inputs():
    class Client:
        def get(self,path,**kwargs):
            assert path=='/fapi/v1/klines'
            assert kwargs==dict(symbol='BTCUSDT',interval='15m',limit=499)
            return api_rows(bars())
    data=lab.fetch_candles('BTCUSDT','15m',Client(),NOW)
    assert len(data)==140 and data[-1]['closed']
    with pytest.raises(ValueError): lab.fetch_candles('../../ETHUSDT','15m',Client(),NOW)
    with pytest.raises(ValueError): lab.fetch_candles('BTCUSDT','1s',Client(),NOW)


def test_unfinished_candle_cannot_change_environment_or_trigger_signal():
    data=bars()
    initial=lab.analyze(data,'15m',now=NOW)
    extreme=data[-1]|dict(time=int(NOW*1000),close_time=int(NOW*1000)+STEP,close=100000.,high=100001.,closed=False)
    result=lab.analyze(data+[extreme],'15m',now=NOW)
    assert result==initial
    assert result['regime']=='上行趋势'
    assert result['signals']==[]  # A trend alone is not a volume-confirmed breakout.


def test_breakout_requires_volume_and_prior_window_excludes_current_bar():
    data=bars()
    data[-1]['quote_volume']=2000
    result=lab.analyze(data,'15m',now=NOW)
    assert result['ready']
    assert result['latest']['volume_ratio']==2
    assert result['signals']==['上行突破候选']
    assert result['latest']['previous_high']<result['latest']['close']


@pytest.mark.parametrize('condition',['gap','stale','short'])
def test_invalid_or_insufficient_data_suppresses_candidate_alarms(condition):
    data=bars()
    data[-1]['quote_volume']=2000
    now=NOW
    if condition=='gap': data.pop(10)
    if condition=='stale': now+=10*STEP/1000
    if condition=='short': data=data[-30:]
    result=lab.analyze(data,'15m',now=now)
    assert not result['ready'] and not result['signals']


def test_flat_market_has_finite_range_metrics():
    result=lab.analyze(bars(flat=True),'15m',now=NOW)
    assert result['regime']=='震荡'
    assert result['latest']['rsi']==50
    assert result['latest']['adx']==0
    assert not result['signals']
    json.dumps(result,allow_nan=False)


def test_export_records_only_closed_observations_and_unvalidated_draft():
    data=bars()
    result=lab.analyze(data,'15m',now=NOW)
    packet=lab.analysis_packet('BTCUSDT','15m',data,result,[{'question':'怎样确认失效？'}],{'backtest_status':'未回测'})
    assert packet['closed_only'] and len(packet['candles'])==140
    assert packet['missing'] and packet['draft']['backtest_status']=='未回测'
    assert packet['questions'][0]['question']=='怎样确认失效？'


def test_csv_epoch_and_iso_time_align_and_source_text_is_never_executed():
    parsed=lab.parse_tv_csv(b'time,close,my_indicator\n1800000000,100,42\n1800000900,101,43\n')
    assert parsed['rows'][0]['time_ms']==1800000000000
    utc=lab.parse_tv_csv(b'time,EMA\n2026-10-25T08:00:00,100\n')
    shanghai=lab.parse_tv_csv(b'time,EMA\n2026-10-25T08:00:00,100\n',timezone(timedelta(hours=8)))
    assert utc['rows'][0]['time_ms']-shanghai['rows'][0]['time_ms']==8*3600000
    payload=b'time,indicator\n1800000000,"__import__(\"\"os\"\").system(\"\"boom\"\")"\n'
    assert 'system' in lab.parse_tv_csv(payload)['rows'][0]['indicator']


@pytest.mark.parametrize('data',[b'x,y\n1,2\n',b'time,v\nbad,1\n',b'time,v\n1800000000,1\n1800000000,2\n'])
def test_csv_without_unambiguous_times_is_rejected(data):
    with pytest.raises(ValueError): lab.parse_tv_csv(data)
