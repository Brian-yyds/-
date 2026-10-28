"""A single-purpose alarm screen: three rankings, one alarm rule, sidebar controls."""
import html
import math
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from urllib.parse import urlsplit
import streamlit as st
import gmgn_monitor as gmgn
import monitoring
import store
import volume_monitor as volume

ZONE=timezone(timedelta(hours=8))
STYLE='''<style>
.stApp{background:#0b1017;color:#e6edf5}
[data-testid="stSidebar"]{background:#101923;border-right:1px solid #253142}
[data-testid="stMainBlockContainer"]{padding:4.25rem clamp(.75rem,2.2vw,2rem) 1.5rem;max-width:none;min-width:0}
/* Streamlit normally shows the collapse control only on hover. Keep both
   native controls visible, including the reopen control after collapsing. */
[data-testid="stSidebarCollapseButton"]{visibility:visible!important;width:auto!important;margin-left:auto}
button[aria-label="Collapse sidebar"],button[aria-label="Expand sidebar"]{visibility:visible!important;opacity:1!important;width:auto;min-height:2rem;padding:4px 9px;border:1px solid #39516c;border-radius:7px;background:#152232;color:#d5e5f5;gap:4px}
button[aria-label="Collapse sidebar"]::before{content:"收起设置";font-size:12px;white-space:nowrap}
button[aria-label="Expand sidebar"]::before{content:"展开设置";font-size:12px;white-space:nowrap}
/* Keep existing content readable during periodic fragment updates. Connection
   and data freshness are reported separately by the live status and warnings. */
[data-testid="stElementContainer"][data-stale]{opacity:1!important;transition:none!important}
h1,h2,h3{letter-spacing:-.025em}
.screen-heading{display:flex;align-items:baseline;gap:8px 20px;margin:0 0 5px;flex-wrap:wrap}
.screen-heading h1{font-size:clamp(1.25rem,2.2vw,1.9rem)!important;padding:0;margin:0;font-weight:750;line-height:1.3}
.screen-heading>span,.muted{color:#8294a9;font-size:clamp(.75rem,1vw,.85rem)}
.screen-heading h1 span,.rank-title h2 span{font-size:inherit!important;color:inherit}
.live-line{display:flex;align-items:center;gap:7px 12px;color:#9eafc3;font-size:clamp(.75rem,1vw,.85rem);margin:8px 0 16px;flex-wrap:wrap;overflow-wrap:anywhere}
.live{color:#a9dc85}.offline{color:#ffbe77}.pending-label{color:#ff858d;margin-left:auto;font-weight:650}
.pending-label.quiet-label{color:#7f9489;font-weight:400}
.pending-label a.contract-link{color:inherit!important}
.market-panel{container-type:inline-size;container-name:market-panel;min-width:0;width:100%}
.top-strip{margin:0 0 14px;max-width:1640px}
.quick-prices{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,220px));gap:10px;min-width:0}
.quick-price{min-width:0;padding:10px 12px;background:#111b27;border:1px solid #2c4057;border-radius:10px;font-variant-numeric:tabular-nums}
.focus-change{font-size:11px;color:#8fa3bb;margin:3px 0 5px}.focus-change b{font-size:14px;margin-left:5px}
.quick-price-name{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px;color:#91a7c0}
.quick-price-value{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:5px 0 4px}
.quick-price-value strong{font-size:clamp(1.25rem,2.1cqi,1.85rem);line-height:1.3;letter-spacing:-.03em;color:#f2f7fc}
.quick-price-value>span{font-size:11px;color:#8298b1}
.quick-price-status{font-size:10px;color:#8396ad}.quick-price-status.is-live{color:#a9dc85}
.quick-price.is-delayed .quick-price-value strong{color:#9babbf}
.overview-strip{max-width:1640px;margin-bottom:10px}.overview-strip .top-strip{margin:0 0 9px}
.overview-strip .quick-prices{grid-template-columns:repeat(auto-fit,minmax(145px,210px));gap:9px}
.volume-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;min-width:0}
.market-volume{padding:8px 10px;background:linear-gradient(115deg,#142438,#111c29);border:1px solid #355475;border-radius:9px;min-width:0;font-variant-numeric:tabular-nums}
.volume-heading{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;color:#c5daef;font-size:12px;font-weight:650;line-height:15px}
.volume-heading small{font-size:9px;color:#8ba5c1;font-weight:400}
.volume-bars{display:grid;grid-template-columns:repeat(15,minmax(0,1fr));gap:2px;height:81px;align-items:end;margin-top:4px}
.volume-day{min-width:0;text-align:center;display:flex;flex-direction:column;justify-content:end;height:100%;gap:3px}
.volume-bar-space{height:64px;padding-top:16px;box-sizing:border-box;display:flex;align-items:end;justify-content:center}.volume-bar{position:relative;width:100%;max-width:20px;border-radius:3px 3px 0 0;background:#608fbd;min-height:2px}
.volume-value{position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:3px;color:#c9def3;font-size:9px;font-weight:600;line-height:11px;letter-spacing:-.04em;white-space:nowrap}
.market-volume.is-top10 .volume-bar{background:#919bd0}.market-volume.is-top10 .volume-heading{color:#cbd1f7}
.volume-day.is-today .volume-bar{background:#b4ed78}.volume-day.is-today .volume-value{color:#c0ea95}
.volume-day span{font-size:8px;color:#8fa7c1;white-space:nowrap;line-height:10px}.volume-day.is-today span{color:#b4d494}
.volume-loading{color:#9cb5ce;font-size:12px;padding:20px 0;line-height:1.6}.market-volume.is-delayed .volume-heading{color:#ffbe77}
@container market-panel (max-width:800px){.volume-pair{grid-template-columns:1fr}}
@container market-panel (max-width:390px){.volume-value{font-size:8px}.volume-bars{gap:1px}}
.gmgn-card{min-width:0;background:#111e22;border:1px solid #33594d;border-radius:10px;padding:10px 12px;font-size:11px}
.gmgn-card.leader-card{background:#111e22;border-color:#33594d}
.gmgn-card.leader-card .gmgn-head{color:#a4dcb5}
.gmgn-card.history-card{background:#281d21;border-color:#b06371}
.gmgn-card.history-card .gmgn-head{color:#ff9aa6}
.gmgn-card.is-red,.gmgn-card.has-alert{background:#281d21;border-color:#b06371}
.gmgn-head{display:flex;justify-content:space-between;align-items:center;gap:8px;color:#a4dcb5;font-weight:650;font-size:12px;flex-wrap:wrap}
.gmgn-card.is-red .gmgn-head,.gmgn-card.has-alert .gmgn-head{color:#ff9aa6}
.gmgn-card.is-red .gmgn-head small{color:#c598a2}
.gmgn-head small{font-weight:400;font-size:10px;color:#8daba0}
.gmgn-feed{max-height:122px;overflow:auto;scrollbar-width:thin}
.gmgn-token{display:block;padding-top:7px;color:#e6edf5!important;text-decoration:none!important;min-width:0}
.gmgn-token+.gmgn-token{border-top:1px solid #453139;margin-top:8px}
.gmgn-identity{display:flex;gap:8px;align-items:center;min-width:0}
.gmgn-logo{width:30px;height:30px;border-radius:50%;object-fit:cover;background:#223b36;flex-shrink:0}
.gmgn-logo-fallback{display:grid;place-items:center;font-weight:700;color:#a6c9b0}
.gmgn-name{min-width:0;flex:1;overflow-wrap:anywhere;font-size:12px;line-height:1.3}
.gmgn-name strong{font-size:14px}.gmgn-name small{color:#9aacae;margin-left:6px;font-size:10px}
.gmgn-metrics{display:flex;gap:4px 12px;flex-wrap:wrap;color:#96aaa7;margin-top:6px;font-size:10px}
.gmgn-metrics b{color:#e2f1e5;font-size:12px;font-variant-numeric:tabular-nums}
.gmgn-ca{font-size:10px;line-height:1.4;color:#8ba49b;overflow-wrap:anywhere;margin-top:4px;user-select:text}
.gmgn-foot{display:flex;justify-content:space-between;gap:4px 10px;flex-wrap:wrap;color:#8da49b;font-size:10px;margin-top:5px;line-height:1.3}
.gmgn-empty{padding:13px 0 7px;color:#9aaea8;font-size:12px;line-height:1.6}
.gmgn-token:hover .gmgn-name strong{color:#b4ed78}.gmgn-token:focus-visible{outline:2px solid #b4ed78;outline-offset:2px}
.gmgn-mini{display:grid;grid-template-columns:28px minmax(0,1fr);gap:5px 8px;padding-top:8px}
.gmgn-mini .gmgn-logo{width:28px;height:28px;grid-row:1/3;align-self:center}
.gmgn-mini-name{font-size:12px;font-weight:650;overflow-wrap:anywhere;line-height:1.25}
.gmgn-mini-name small{font-size:9px;color:#8fa39e;font-weight:400;margin-left:5px}
.gmgn-mini-stats{display:flex;align-items:baseline;justify-content:space-between;gap:3px 8px;flex-wrap:wrap;font-size:10px;color:#94aaa2;line-height:1.4}
.gmgn-mini-stats b{font-size:12px;color:#e3f0e8;font-variant-numeric:tabular-nums}
.gmgn-mini+.gmgn-mini{border-top:1px solid #46323b;margin-top:6px;padding-top:6px}
.gmgn-card.history-card .gmgn-mini-stats{color:#bba1aa}
.gmgn-card .gmgn-empty{padding:10px 0 2px;font-size:11px}
.rankings-grid{--rank-row:40px;--rank-head:32px;--panel-body:calc(var(--rank-head) + 5 * var(--rank-row));display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) minmax(0,1.65fr);gap:10px;align-items:stretch;max-width:1640px}
.rank-card,.alarm-card{background:#111b27;border:1px solid #29374a;border-radius:12px;overflow:hidden;min-width:0}
.rank-title{height:52px;box-sizing:border-box;padding:8px 10px;border-bottom:1px solid #263346;display:flex;align-items:center;justify-content:space-between;gap:4px;flex-wrap:wrap}
.rank-title h2{font-size:clamp(.95rem,1.15cqi,1.2rem)!important;font-weight:750;margin:0;padding:0;line-height:1.3}.rank-title>span{font-size:10px;color:#8fa2ba}
.rank-title h2.oi-accent{color:#83c3ff!important}.rank-title h2.up-accent{color:#9de57c!important}.rank-title h2.down-accent{color:#ff8e99!important}
.oi-accent{color:#83c3ff}.up-accent{color:#9de57c}.down-accent{color:#ff8e99}
.rank-scroll{height:var(--panel-body);overflow-y:auto;scrollbar-width:thin;scrollbar-color:#344457 #111b27}
.rank-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;table-layout:fixed}
.rank-table th{height:var(--rank-head);box-sizing:border-box;position:sticky;top:0;background:#152232;color:#8fa5be;font-size:11px;font-weight:500;z-index:1;line-height:1.25;overflow-wrap:anywhere}
.rank-table th,.rank-table td{padding:5px 4px;text-align:right;border-bottom:1px solid #1f2b3b;vertical-align:middle}
.rank-table col:nth-child(1){width:10%}.rank-table col:nth-child(2){width:34%}.rank-table col:nth-child(3){width:33%}.rank-table col:nth-child(4){width:23%}
.rank-table td:first-child,.rank-table th:first-child{white-space:nowrap;padding-left:5px;padding-right:3px}
.rank-table td{height:var(--rank-row);box-sizing:border-box;font-size:clamp(.75rem,.9cqi,.9rem);line-height:1.25;overflow-wrap:anywhere}.rank-table td:first-child,.rank-table th:first-child{color:#6f829b;text-align:left}
.rank-table td:nth-child(2),.rank-table th:nth-child(2){text-align:left}
.coin{font-weight:650;color:#e6edf5}.star{color:#f4d27d}.rank-table td.move{font-size:clamp(.8rem,1cqi,1.05rem);font-weight:750;line-height:1.25;white-space:nowrap}
a.contract-link,a.contract-link:visited{color:#e6edf5!important;text-decoration:none!important;overflow-wrap:anywhere;line-height:1.45}
a.contract-link:hover{color:#b4ed78!important;text-decoration:underline!important}a.contract-link:focus-visible{outline:2px solid #b4ed78;outline-offset:3px;border-radius:3px}
.stock-tag{font-size:10px;color:#93c5fd;border:1px solid #395878;border-radius:4px;margin-left:5px;padding:1px 3px;vertical-align:middle}
.rank-table tbody tr:hover{background:#182638}.empty{text-align:center;color:#8195af;padding:72px 12px;font-size:14px}
.alarm-card{border-color:#633b49;background:#211820}.alarm-card .rank-title{border-color:#49303a}
.alarm-card .rank-title h2{color:#ff9aa6!important;font-size:clamp(.9rem,1cqi,1.1rem)!important}
.alarm-card .rank-title>span{color:#d3a4ae}
.alarm-box{height:var(--panel-body);overflow-y:auto;scrollbar-width:thin;scrollbar-color:#664453 #211820}
.alarm-entry{padding:8px 10px;border-bottom:1px solid #49303a;font-variant-numeric:tabular-nums}
.alarm-entry-head{display:flex;align-items:baseline;justify-content:space-between;gap:6px;flex-wrap:wrap;font-size:13px}
.alarm-entry-head strong{font-size:15px;white-space:nowrap}
.alarm-entry-meta{display:flex;justify-content:space-between;gap:4px 8px;flex-wrap:wrap;font-size:10px;color:#b69ca6;margin-top:4px;overflow-wrap:anywhere}
.alarm-empty{padding:24px 14px;color:#bca4ad;font-size:13px;line-height:1.8}.alarm-empty strong{display:block;color:#9bbfa4;margin-bottom:8px}
.alarm-more{padding:10px;color:#b69ca6;font-size:11px}
.gmgn-panel{background:#111e22;border:1px solid #33594d;border-radius:12px;overflow:hidden;min-width:0}
.gmgn-panel>.rank-title{border-color:#33594d}.gmgn-panel h2{color:#a4dcb5!important}
.gmgn-combined{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);min-height:var(--panel-body)}
.gmgn-leaders{display:grid;grid-template-rows:repeat(2,minmax(0,1fr));min-width:0}
.gmgn-combined .gmgn-card{border:0;border-radius:0;padding:8px;min-width:0}
.gmgn-leaders .leader-card+.leader-card{border-top:1px solid #33594d}
.gmgn-combined .history-card{border-left:1px solid #633b49}
.gmgn-combined .gmgn-head{font-size:11px;gap:3px}.gmgn-combined .gmgn-head small{font-size:9px}
.gmgn-combined .gmgn-mini{grid-template-columns:26px minmax(0,1fr);gap:4px 5px}
.gmgn-combined .gmgn-mini .gmgn-logo{width:26px;height:26px;grid-row:auto}
.gmgn-combined .gmgn-mini-stats{grid-column:1/-1;justify-content:flex-start;font-size:10px;gap:2px 6px}
.gmgn-combined .gmgn-mini-stats b{font-size:12px}
.gmgn-combined .gmgn-mini-name{font-size:12px;align-self:center}.gmgn-combined .gmgn-empty{font-size:11px}
@container market-panel (max-width:1100px){.rankings-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.gmgn-panel{grid-column:1/-1}.gmgn-combined{min-height:190px}}
@container market-panel (max-width:640px){
 .rankings-grid{grid-template-columns:minmax(0,1fr)}
 .rank-title h2{font-size:1.25rem!important}
 .rank-table td{font-size:.85rem}.rank-table td.move{font-size:1rem}
 .quick-prices{gap:8px;grid-template-columns:repeat(2,minmax(0,1fr))}
 .quick-price{padding:10px}
 .quick-price-value strong{font-size:clamp(.95rem,3.5cqi,1.3rem)}
 .gmgn-feed{max-height:180px}
 .gmgn-panel{grid-column:auto}
}
@container market-panel (max-width:460px){
 .quick-prices{grid-template-columns:minmax(0,1fr)}
 .quick-price{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;align-items:center}
 .quick-price-value{margin:0}.quick-price-value strong{font-size:1.3rem}
 .quick-price-status{grid-column:1/-1}.focus-change{margin:0}
}
@media(max-width:640px){.pending-label{margin-left:0;flex-basis:100%}.screen-heading{align-items:flex-start;flex-direction:column}}
</style>'''


def amount(value):
    if value is None: return '—'
    if abs(value)>=1e8: return f'{value/1e8:.1f}亿'
    if abs(value)>=1e4: return f'{value/1e4:.0f}万'
    return f'{value:,.0f}'


def clock(ms):
    return datetime.fromtimestamp(ms/1000,ZONE).strftime('%m-%d %H:%M:%S')


def coin(symbol,priority=False,stock=False,commodity=False):
    tag='<span class="stock-tag" title="股票类合约（含ETF及盘前产品）">股</span>' if stock else '<span class="stock-tag" title="大宗商品合约">商</span>' if commodity else ''
    label=(' <span class="star">★</span>' if priority else '')+html.escape(symbol.removesuffix('USDT'))+tag
    url='https://www.binance.com/zh-CN/futures/'+quote(symbol,safe='')
    return f'<a class="contract-link" href="{url}" target="_blank" rel="noopener noreferrer" title="{html.escape(symbol,quote=True)} · 打开币安合约交易页">{label}</a>'


def market_volume_card(now=None):
    now=time.time() if now is None else now
    snapshot=store.get(volume.SNAPSHOT_KEY,{})
    health=store.get('market_volume_health',{})
    ready=bool(snapshot.get('complete') and snapshot.get('range_start')==volume.window_start(now) and len(snapshot.get('days',[]))==volume.DAYS)
    cards=[]
    maximum=max((d['quote'] for d in snapshot.get('days',[])),default=0)
    for kind,title,field in [('all','加密全市场 · 15日成交额','quote'),('top10','加密每日前10 · 15日成交额','top10_quote')]:
        heading=f'<div class="volume-heading"><span>{title}</span><small>亿USDT · 含今日</small></div>'
        if not ready:
            count=health.get('total',0)
            progress=f'（{health.get("completed",0)}/{count}）' if count else ''
            cards.append(f'<section class="market-volume" data-volume-kind="{kind}">'+heading+f'<div class="volume-loading">15日数据补齐中{progress}</div></section>')
            continue
        bars=[]
        for i,day in enumerate(snapshot['days']):
            date=datetime.fromtimestamp(day['start']/1000,ZONE)
            value=day[field]
            tip=f'{date:%Y-%m-%d}'+(' 今日累计（未结束）' if i==volume.DAYS-1 else '')+f' · {value:,.2f} USDT'
            if kind=='top10': tip+='\n'+ '、'.join(f'{t["symbol"]}: {t["quote"]/1e8:.2f}亿' for t in day['top10'])
            height=max(2,value/maximum*46) if maximum else 2
            label='今日' if i==volume.DAYS-1 else f'{date:%m/%d}'
            bars.append(f'<div class="volume-day{" is-today" if i==volume.DAYS-1 else ""}" title="{html.escape(tip,quote=True)}"><div class="volume-bar-space"><div class="volume-bar" style="height:{height:.2f}px"><b class="volume-value">{value/1e8:.1f}</b></div></div><span>{label}</span></div>')
        delayed=now-snapshot['updated_at']>1200 or health.get('status') not in ('统计完成','正在汇总')
        updated=datetime.fromtimestamp(snapshot['updated_at'],ZONE).strftime('%m/%d %H:%M')
        detail=f'北京时间15个自然日，今日未结束；仅统计加密USDT永续（含BTC/ETH），排除币股、大宗和ETF；{updated}更新。两图同比例。'
        if kind=='top10':detail+='每一天单独按当日成交额取前10名，悬停柱子可看成分。'
        if delayed:detail+='当前更新延迟。'
        cards.append(f'<section class="market-volume{" is-top10" if kind=="top10" else ""}{" is-delayed" if delayed else ""}" data-volume-kind="{kind}" title="{detail}">'+heading+f'<div class="volume-bars" role="img" aria-label="{title}">'+''.join(bars)+'</div></section>')
    return '<div class="volume-pair" data-market-volume="'+('ready' if ready else 'loading')+'">'+''.join(cards)+'</div>'


def quick_prices(now=None):
    now=time.time() if now is None else now
    cfg=store.settings()
    symbols=store.filter_stock_symbols(cfg['top_symbols'],cfg)
    quotes=monitoring.focus_quotes(symbols,now)
    stocks=set(store.get('exchange_info',{}).get('stock_symbols',[]))
    commodities=set(store.get('exchange_info',{}).get('commodity_symbols',[]))
    cards=[]
    for symbol in symbols:
        tick=quotes[symbol]
        price=tick.get('price')
        stamp=tick.get('event_time',0)
        valid=isinstance(price,(int,float)) and math.isfinite(price) and price>0
        fresh=tick['fresh']
        value=(f'{price:,.2f}' if price>=1 else f'{price:.6g}') if valid else '—'
        status=('实时 · ' if fresh else '行情延迟 · ')+clock(stamp) if valid and stamp else '等待行情'
        change=tick['change']
        change_text=f'{change:+.2f}%' if change is not None else '—'
        change_css='up-accent' if change is not None and change>=0 else 'down-accent' if change is not None else 'muted'
        cards.append(f'<div class="quick-price{" is-delayed" if not fresh else ""}" data-symbol="{symbol}">'
                     f'<div class="quick-price-name">{coin(symbol,priority=True,stock=symbol in stocks,commodity=symbol in commodities)}<span>重点监控</span></div>'
                     f'<div class="quick-price-value"><strong>{value}</strong><span>USDT</span></div>'
                     f'<div class="focus-change" title="{html.escape(tick["basis"],quote=True)}">近4h <b class="{change_css}">{change_text}</b></div>'
                     f'<div class="quick-price-status{" is-live" if fresh else ""}">{status}</div></div>')
    prices='<div class="quick-prices">'+''.join(cards)+'</div>'
    return '<div class="market-panel"><div class="overview-strip"><div class="top-strip">'+prices+'</div>'+market_volume_card(now)+'</div></div>'


def gmgn_token_preview(token,tooltip='打开 GMGN'):
    url=gmgn.token_url(token)
    if not url: return ''
    esc=lambda value:html.escape(str(value or ''),quote=True)
    logo=token.get('logo','')
    try:
        parsed=urlsplit(logo)
        safe_image=parsed.scheme=='https' and not parsed.username and not parsed.password and (parsed.hostname=='gmgn.ai' or (parsed.hostname or '').endswith('.gmgn.ai'))
    except ValueError:
        safe_image=False
    image=f'<img class="gmgn-logo" src="{esc(logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">' if safe_image else '<span class="gmgn-logo gmgn-logo-fallback">G</span>'
    cap=token.get('market_cap')
    value='—' if cap is None else f'${cap/1e6:.2f}M' if cap>=1e6 else f'${cap/1e3:.1f}K' if cap>=1000 else f'${cap:,.0f}'
    holders=f'{token["holder_count"]:,.0f}' if token.get('holder_count') is not None else '—'
    chain=gmgn.CHAIN_LABELS.get(token['chain'],token['chain'].upper())
    tip=tooltip+' · '+token.get('name','')+' · '+chain+' · '+token['address']
    return (f'<a class="gmgn-token gmgn-mini" href="{url}" target="_blank" rel="noopener noreferrer" title="{esc(tip)}">'
            f'{image}<div class="gmgn-mini-name">{esc(token.get("symbol") or token.get("name"))}<small>{esc(chain)}</small></div>'
            f'<div class="gmgn-mini-stats"><span>市值 <b>{value}</b></span><span>持有人 <b>{holders}</b></span></div></a>')


def gmgn_alarm_card():
    records=gmgn.recent_alerts(3)
    body=''.join(gmgn_token_preview(r['token'],r['rule']+' · '+clock(r['triggered']*1000)) for r in records)
    if not body: body='<div class="gmgn-empty">暂无历史警报</div>'
    return '<section class="gmgn-card history-card" data-gmgn-kind="alarms"><div class="gmgn-head"><span>GMGN 警报</span><small>最近3条</small></div>'+body+'</section>'


def gmgn_card(now=None,kind='hot',snapshot=None,show_status=True):
    now=time.time() if now is None else now
    snapshot=store.get('gmgn_market',{}) if snapshot is None else snapshot
    fresh=0<=now-snapshot.get('hot_updated' if kind=='hot' else 'last_success',0)<=60
    idle_label='热搜 #1' if kind=='hot' else '5m 成交额 #1'
    idle=snapshot.get('hot' if kind=='hot' else 'volume_leader')
    cfg=gmgn.configuration()
    eligible=(snapshot.get('hot_scope')==gmgn.HOT_SEARCH_SCOPE) if kind=='hot' else (idle and gmgn.leader_eligible(idle,now,cfg))
    empty='正在更新四链热搜' if kind=='hot' and not eligible else '暂无热搜数据' if kind=='hot' else '暂无符合条件的代币'
    tooltip=(f'热搜名单 · 5m成交额 ${idle["volume"]:,.0f} · 更新 {clock(snapshot["hot_updated"]*1000)}'
             if kind=='hot' and idle and eligible and idle.get('volume') is not None and snapshot.get('hot_updated') else '打开 GMGN')
    body=gmgn_token_preview(idle,tooltip) if idle and eligible else f'<div class="gmgn-empty">{empty}</div>'
    status=snapshot.get('status','正在连接')
    if not gmgn.configuration()['enabled']: status='已暂停 GMGN 监控'
    if status in ('监控中','正在扫描四链') and not fresh: status='行情延迟'
    if snapshot.get('retry_at',0)>now: status+=' · '+clock(snapshot['retry_at']*1000).split(' ')[-1]+' 重试'
    capped=any(v.get('capped') for v in snapshot.get('chains',{}).values())
    if capped: status+=' · 榜单达100条上限'
    normal=status in ('监控中','正在扫描四链') and fresh and not capped
    notice='' if normal or not show_status else f'<div class="gmgn-foot"><span>{html.escape(status)}</span></div>'
    bounds='5m 成交额 ↓' if kind=='hot' else f'{cfg["leader_days"]}天 · ≤{cfg["leader_cap"]/1e6:g}M'
    tip='SOL / BSC / Base / ROB 四链热搜名单，按5分钟美元成交额降序；不加天数、市值或警报筛选' if kind=='hot' else '成交榜首按创建时间和市值筛选'
    return f'<section class="gmgn-card leader-card" data-gmgn-kind="{kind}"><div class="gmgn-head"><span>{idle_label}</span><small title="{tip}">{bounds}</small></div>'+body+notice+'</section>'


def gmgn_combined_card(now=None):
    now=time.time() if now is None else now
    snapshot=store.get('gmgn_market',{})
    leaders=''.join(gmgn_card(now,kind,snapshot,show_status=False) for kind in ('hot','volume'))
    status=snapshot.get('status','正在连接')
    if not gmgn.configuration()['enabled']: status='已暂停'
    elif status in ('监控中','正在扫描四链'):
        status='SOL / BSC / BASE / ROB' if 0<=now-snapshot.get('last_success',0)<=60 else '数据延迟'
    elif '限频' in status: status='限频等待恢复'
    if any(v.get('capped') for v in snapshot.get('chains',{}).values()): status+=' · 榜单上限'
    return ('<section class="gmgn-panel"><div class="rank-title"><h2>GMGN 监控</h2><span title="详情见设置与调试">'+html.escape(status)+'</span></div>'
            '<div class="gmgn-combined"><div class="gmgn-leaders">'+leaders+'</div>'+gmgn_alarm_card()+'</div></section>')


def ranking_card(rows,title,kind):
    oi=kind.startswith('oi')
    accent='oi-accent' if oi else 'up-accent' if kind=='up' else 'down-accent'
    label='近4h增仓' if kind=='oi_up' else '近4h减仓' if kind=='oi_down' else '近4h涨幅' if kind=='up' else '近4h跌幅'
    body=[]
    for index,r in enumerate(rows,1):
        value=r['oi_change'] if oi else r['price_change']
        approximate=oi and r['oi_basis']!='实时采样'
        tip=f"{r['oi_basis']} · 回看{r['oi_span']:.1f}分钟" if oi else f"回看{r['price_span']:.2f}分钟" if r.get('price_span') is not None else ''
        body.append(f'<tr><td>{index:02d}</td><td class="coin">{coin(r["symbol"],r["priority"],r.get("asset_type")=="stock",r.get("asset_type")=="commodity")}</td>'
                    f'<td class="move {accent}" title="{html.escape(tip,quote=True)}">{"≈" if approximate else ""}{value:+.2f}%</td>'
                    f'<td class="muted">{amount(r["quote"])}</td></tr>')
    content=('<table class="rank-table"><colgroup><col><col><col><col></colgroup><thead><tr><th>#</th><th>标的</th><th>'+label+'</th><th title="近4小时 USDT 成交额">成交额</th></tr></thead><tbody>'+''.join(body)+'</tbody></table>') if body else '<div class="empty">等待有效数据</div>'
    return f'<section class="rank-card"><div class="rank-title"><h2 class="{accent}">{title}</h2><span title="默认显示前5名，滚动查看其余排名">4 小时 · 滚动更多</span></div><div class="rank-scroll">{content}</div></section>'


def alarm_card(records,cfg):
    records=monitoring.monitored_spike_alerts(records,cfg)
    body=[]
    stocks=set(store.get('exchange_info',{}).get('stock_symbols',[]))
    commodities=set(store.get('exchange_info',{}).get('commodity_symbols',[]))
    for r in records[:200]:
        ident,stamp,symbol,direction,change,base,price,base_time,event_time,basis,ack=r
        color='up-accent' if direction=='急涨' else 'down-accent'
        body.append(f'<div class="alarm-entry"><div class="alarm-entry-head"><span class="coin">{coin(symbol,stock=symbol in stocks,commodity=symbol in commodities)}</span>'
                    f'<strong class="{color}">{html.escape(direction)} {change:+.2f}%</strong></div>'
                    f'<div class="alarm-entry-meta"><time>{clock(event_time)}</time><span>触发价 {price:.8g}</span></div></div>')
    if records:
        content=''.join(body)
        if len(records)>200: content+='<div class="alarm-more">显示最新200条，其余报警仍保留待确认。</div>'
    else:
        content=f'<div class="alarm-empty"><strong>暂无待确认报警</strong>15 分钟内急涨 ≥ {cfg["spike_up_pct"]:g}%<br>或急跌 ≥ {cfg["spike_down_pct"]:g}% 时提醒</div>'
    return f'<section class="alarm-card"><div class="rank-title"><h2>15分钟急涨急跌</h2><span>{len(records)} 条待确认</span></div><div class="alarm-box">{content}</div></section>'


def merged_alarms(records):
    seen=set()
    result=[]
    for r in records:
        key=(r[2],r[3])
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


@st.fragment(run_every='3s')
def acknowledgement():
    pending=monitoring.monitored_spike_alerts(store.spike_alerts(unacknowledged=True,limit=10000))
    if st.button(f'确认当前报警（{len(merged_alarms(pending))}）',disabled=not pending,key='ack_spike_alarms',width='stretch'):
        store.acknowledge_spikes([r[0] for r in pending])
        st.rerun()


@st.fragment(run_every='3s')
def gmgn_acknowledgement():
    ids=gmgn.pending_ids()
    if st.button(f'确认 GMGN 报警（{len(gmgn.pending_alerts())}）',disabled=not ids,key='ack_gmgn',width='stretch'):
        gmgn.acknowledge(ids)
        st.rerun()


@st.fragment(run_every='3s')
def board(scope,search,oi_direction,asset_scope='全部'):
    cfg=store.settings()
    rows=monitoring.build_rows(cfg,scope='market')
    health=store.get('monitor_health',{})
    stream=store.get('stream_health',{})
    alive=0<=time.time()-health.get('heartbeat',0)<45
    connected=stream.get('connected',False) and 0<=time.time()-stream.get('last_message',0)<15
    scanner=store.get('market_health',{})
    scanner_alive=0<=time.time()-scanner.get('heartbeat',0)<45
    state='● 监控中' if alive and connected and scanner_alive else '● 轮询监控' if alive and scanner_alive else '● 正在连接 / 请检查服务'
    css='live' if alive and scanner_alive else 'offline'
    pending_records=monitoring.monitored_spike_alerts(store.spike_alerts(unacknowledged=True,limit=10000),cfg)
    pending=merged_alarms(pending_records)
    stock_symbols={r['symbol'] for r in rows if r['asset_type']=='stock'}
    commodity_symbols={r['symbol'] for r in rows if r['asset_type']=='commodity'}
    summary=(coin(pending[0][2],stock=pending[0][2] in stock_symbols,commodity=pending[0][2] in commodity_symbols)+html.escape(f' {pending[0][3]} {pending[0][4]:+.2f}% · {len(pending)} 条待确认')) if pending else '暂无待确认报警'
    summary_css='pending-label' if pending else 'pending-label quiet-label'
    stock_count=len(stock_symbols)
    commodity_count=len(commodity_symbols)
    coverage=f'全市场 {len(rows)} 合约 · 加密 {len(rows)-stock_count-commodity_count} / 股票 {stock_count} / 大宗 {commodity_count}'
    st.markdown(f'<div class="live-line"><span class="{css}">{state}</span><span>{coverage}</span><span>排行 4 小时</span><span>{datetime.now(ZONE):%H:%M:%S}</span><span class="{summary_css}">{summary}</span></div>',unsafe_allow_html=True)
    st.markdown(quick_prices(),unsafe_allow_html=True)
    shown=[r for r in rows if (scope!='仅重点' or r['priority']) and search.upper().strip() in r['symbol']
           and (asset_scope=='全部' or r['asset_type']=={'股票':'stock','加密货币':'crypto','大宗':'commodity'}.get(asset_scope))]
    cards=''.join(ranking_card(monitoring.ranked(shown,kind)[:30],title,kind)
                  for title,kind in zip(['OI 异动榜','上涨榜','下跌榜'],['oi_up' if oi_direction=='增仓' else 'oi_down','up','down']))
    cards+=alarm_card(pending,cfg)+gmgn_combined_card()
    st.markdown('<div class="market-panel"><div class="rankings-grid">'+cards+'</div></div>',unsafe_allow_html=True)
    # Leave the area below this compact row free for future monitoring panels.
    # Rankings are a viewing filter; they must never hide market-wide alarms.
    if not alive or not scanner_alive:
        st.warning('采集服务正在连接或心跳已过期；恢复连接后自动更新。')


def monitor_page():
    view=store.get('monitor_view',{})
    scope='加密、币股/ETF与大宗永续'
    st.markdown('<div class="screen-heading"><h1>全市场异动监控</h1><span>币安 USDT · '+scope+'</span></div>',unsafe_allow_html=True)
    board(view.get('scope','全市场'),view.get('search',''),view.get('oi_direction','增仓'),view.get('asset_scope','全部'))


def settings_page():
    from settings_ui import render_settings
    render_settings()


def render():
    st.set_page_config(page_title='全市场异动监控',page_icon='◉',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(STYLE,unsafe_allow_html=True)
    st.navigation([st.Page(monitor_page,title='监控面板',default=True),
                   st.Page(settings_page,title='设置与调试',url_path='settings')],position='top').run()
