"""Official hosted TradingView chart; analysis keeps its own Binance snapshot."""
import html
import json
import re
from urllib.parse import urlencode

INTERVALS = {'5m': '5', '15m': '15', '1h': '60', '4h': '240'}


def perpetual_symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9_]{2,40}USDT', symbol):
        raise ValueError('请选择有效的USDT永续合约。')
    return 'BINANCE:' + symbol + '.P'


def chart_url(symbol, interval):
    return 'https://cn.tradingview.com/chart/?' + urlencode({
        'symbol': perpetual_symbol(symbol), 'interval': INTERVALS[interval]})


def widget_config(symbol, interval):
    return dict(
        autosize=True, symbol=perpetual_symbol(symbol), interval=INTERVALS[interval],
        timezone='Asia/Shanghai', theme='dark', style='1', locale='zh_CN',
        allow_symbol_change=False, hide_top_toolbar=False, hide_side_toolbar=False,
        hide_legend=False, hide_volume=False, withdateranges=True, save_image=True,
        calendar=False, details=False, hotlist=False, studies=[],
        backgroundColor='#0e1823', gridColor='rgba(46, 64, 85, 0.35)',
        support_host='https://www.tradingview.com')


def widget_html(symbol, interval):
    config = json.dumps(widget_config(symbol, interval), ensure_ascii=True)
    url = html.escape(chart_url(symbol, interval), quote=True)
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;width:100%;height:100%;background:#0e1823;color:#aebed0;font:12px sans-serif;overflow:hidden}
.tradingview-widget-container{height:100%;width:100%}
.tradingview-widget-container__widget{height:calc(100% - 32px);width:100%}
.tradingview-widget-copyright{height:32px;display:flex;align-items:center;justify-content:center;gap:4px}
a{color:#80b8f5;text-decoration:none}
#tv-load-error{position:absolute;top:45%;left:5%;right:5%;text-align:center;line-height:2}
</style></head><body>
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <div class="tradingview-widget-copyright"><a href="''' + url + '''" rel="noopener nofollow" target="_blank">''' + html.escape(symbol) + ''' 永续合约图表</a><span>by TradingView</span></div>
  <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async
    onerror="document.getElementById('tv-load-error').hidden=false;">''' + config + '''</script>
</div>
<div id="tv-load-error" hidden>TradingView暂未加载，请检查网络，或切换到“本地核对图”。<br><a href="''' + url + '''" target="_blank" rel="noopener">打开TradingView完整版 ↗</a></div>
</body></html>'''
