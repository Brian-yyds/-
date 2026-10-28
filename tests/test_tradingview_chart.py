from urllib.parse import parse_qs, urlparse
import pytest
import tradingview_chart as tv


@pytest.mark.parametrize('period,minutes',[('5m','5'),('15m','15'),('1h','60'),('4h','240')])
def test_chart_and_full_page_use_same_binance_perpetual_and_period(period,minutes):
    config=tv.widget_config('ETHUSDT',period)
    link=parse_qs(urlparse(tv.chart_url('ETHUSDT',period)).query)
    assert config['symbol']==link['symbol'][0]=='BINANCE:ETHUSDT.P'
    assert config['interval']==link['interval'][0]==minutes


@pytest.mark.parametrize('symbol',['BTCUSDT</script>','BINANCE:BTCUSDT','BTCUSD','BTCUSDT?key=secret','btcusdt',None])
def test_no_arbitrary_symbols_or_html_enter_embed(symbol):
    with pytest.raises(ValueError): tv.widget_html(symbol,'15m')


def test_hosted_chart_is_chinese_and_keeps_analysis_symbol_fixed():
    config=tv.widget_config('1000PEPEUSDT','15m')
    assert config['symbol']=='BINANCE:1000PEPEUSDT.P'
    assert config['locale']=='zh_CN' and config['timezone']=='Asia/Shanghai'
    assert config['allow_symbol_change'] is False
    assert config['hide_side_toolbar'] is False and config['hide_top_toolbar'] is False
    assert 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js' in tv.widget_html('BTCUSDT','15m')
