"""Three read-only alarm panels render one persisted snapshot, without network."""
from datetime import datetime
import html
import time
from urllib.parse import urlsplit
from pathlib import Path
import streamlit as st
import gmgn_monitor as gmgn
import web3_signals as signals

STYLE='''<style>
.market-rankings.rankings-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
.signal-panels{--feed-body:clamp(420px,calc((100dvh - 640px) * 1.25),525px);display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;min-width:0;margin-bottom:10px}
.signal-panel{min-width:0;border:1px solid #3b514c;background:#1c292b;border-radius:12px;overflow:hidden}
.signal-panel.wallet-panel{background:#202a32;border-color:#435365}
.signal-panel .rank-title{height:46px;flex-wrap:nowrap;border-color:#34464a;padding:8px 12px}
.signal-panel .rank-title h2{font-size:17px!important;color:#94b5aa!important}
.signal-panel.wallet-panel .rank-title h2{color:#a5b6c9!important}
.signal-panel .rank-title>span{font-size:10px;text-align:right;line-height:1.4;color:#8ea4ad}
.signal-list{height:var(--feed-body);overflow-y:auto;scrollbar-width:thin;scrollbar-color:#405663 #1c292b}
.signal-entry{display:block!important;padding:9px 14px;border-bottom:1px solid #36464b;color:#b6c8ce!important;text-decoration:none!important;font-variant-numeric:tabular-nums;min-width:0}
.signal-entry:hover{background:#26353d}.signal-entry:focus-visible{outline:2px solid #91b5a0;outline-offset:-2px}
.signal-entry-head{display:flex;align-items:center;gap:9px;min-width:0}
.signal-avatar{width:33px;height:33px;flex:none;border-radius:50%;object-fit:cover;background:#2c3d45;color:#91a8b7;display:grid;place-items:center;font-size:14px}
.signal-token-name{min-width:0;flex:1;overflow-wrap:anywhere}.signal-token-name b{font-size:16px;font-weight:650;line-height:1.4}
.signal-token-name small{font-size:10px;margin-left:7px;color:#8ba2ae}
.signal-badge{flex:none;color:#97b9a5;font-size:11px;border:1px solid #455951;border-radius:5px;padding:2px 5px}
.signal-badge.sell{color:#c69a9e;border-color:#65474d}.signal-badge.inactive{color:#8999a5;border-color:#3f4b55}
.signal-numbers{display:flex;justify-content:space-between;align-items:baseline;gap:6px 14px;flex-wrap:wrap;margin:9px 0 6px;font-size:11px;color:#90a7b1}
.signal-numbers b{font-size:20px;color:#b9ccd2;margin-left:5px;font-weight:650;white-space:nowrap}
.signal-time{display:flex;justify-content:space-between;gap:5px 12px;flex-wrap:wrap;font-size:11px;color:#92a7b3;line-height:1.7}
.signal-time time{color:#b3a4a3;font-variant-numeric:tabular-nums}
.signal-address{color:#788f9d;font-size:10px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.signal-empty{padding:20px 15px;font-size:13px;color:#8ea5ae;line-height:1.8}.signal-empty strong{display:block;color:#a7bdb9;font-weight:500;margin-bottom:5px}
.signal-entry.is-expired .signal-token-name b{color:#93a4af}
.special-panel{border:1px solid #6a5b40;background:#2a2925;border-radius:12px;overflow:hidden;margin:0;min-width:0}
.special-panel>.rank-title{height:46px;border-color:#4d493c;padding:8px 12px}.special-panel>.rank-title h2{color:#c4b28a!important;font-size:17px!important}
.special-list{display:block;height:var(--feed-body);overflow-y:auto;scrollbar-width:thin;scrollbar-color:#695e47 #2a2925}
.special-list .signal-entry{padding:11px 12px;border-color:#4d493c;border-right:1px solid #4d493c}
.special-list .signal-numbers{margin:5px 0}.special-list .signal-numbers b{font-size:18px}.special-list .signal-token-name b{font-size:16px}
.special-list .signal-avatar{width:27px;height:27px}.special-list .signal-time{font-size:10px;gap:2px 8px}
.special-list .signal-badge{font-size:10px}.special-list .signal-address{margin-top:2px}.special-list>.signal-empty{grid-column:1/-1;align-self:center;padding:14px 18px}
.signal-list>.signal-entry,.special-list>.signal-entry{box-sizing:border-box;min-height:calc(var(--feed-body) / 3);display:flex!important;flex-direction:column;justify-content:center;padding-top:14px;padding-bottom:14px}
.st-key-signal-scroll-memory{display:none!important}
[data-testid="stLayoutWrapper"]:has(.st-key-signal-scroll-memory){display:none!important}
.spike-mini .rank-title h2{color:#c5ad87!important}.spike-mini .ranking-primary strong{font-size:25px}.spike-mini .ranking-footer{font-size:11px;color:#8fa5b6;flex-wrap:wrap}
@container market-panel (max-width:760px){.signal-panels{grid-template-columns:1fr;--feed-body:450px}.market-rankings.rankings-grid{grid-template-columns:1fr}.signal-entry .signal-token-name b{font-size:17px}}
</style>'''


def esc(value):return html.escape(str(value or ''),quote=True)


def usd(value):
    if value is None:return '—'
    if value>=1e6:return f'${value/1e6:.2f}M'
    if value>=1e3:return f'${value/1e3:.1f}K'
    return f'${value:,.0f}'


def clock(stamp):
    return datetime.fromtimestamp(stamp,gmgn.ZONE).strftime('%m-%d %H:%M:%S') if stamp else '—'


def avatar(token):
    logo=token.get('logo','')
    try:
        parsed=urlsplit(logo);host=parsed.hostname or ''
        safe=(parsed.scheme=='https' and not parsed.username and not parsed.password
              and (host=='bin.bnbstatic.com' or host=='gmgn.ai' or host.endswith('.gmgn.ai')))
    except ValueError:safe=False
    return (f'<img class="signal-avatar" src="{esc(logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            if safe else '<span class="signal-avatar" aria-hidden="true">◉</span>')


def entry(token,stats,times,badge='',inactive=False,event_key=''):
    url=gmgn.token_url(token)
    if not url:return ''
    chain=gmgn.CHAIN_LABELS.get(token['chain'],token['chain'])
    return (f'<a class="signal-entry{" is-expired" if inactive else ""}" data-entry-key="{esc(event_key or token["chain"]+":"+token["address"])}" href="{url}" target="_blank" rel="noopener noreferrer" title="查看代币 · {esc(token["address"])}">'
            '<div class="signal-entry-head">'+avatar(token)+f'<div class="signal-token-name"><b>{esc(token.get("symbol") or token.get("name") or "未命名")}</b><small>{chain}</small></div>'+badge+'</div>'
            '<div class="signal-numbers">'+stats+'</div><div class="signal-time">'+times+'</div>'
            f'<div class="signal-address">CA {esc(token["address"])}</div></a>')


def gmgn_status(snapshot,now):
    if not gmgn.configuration()['enabled']:return '已暂停'
    states=snapshot.get('gmgn_health',{}).get('chains',{})
    delayed=[gmgn.CHAIN_LABELS[c] for c in gmgn.CHAINS if states.get(c,{}).get('error') or not 0<=now-states.get(c,{}).get('updated',0)<=180]
    result='/'.join(delayed)+' 更新延迟' if delayed else '四链已更新'
    if any(states.get(c,{}).get('capped') for c in gmgn.CHAINS):result+=' · 候选有限'
    return result


def gmgn_card(snapshot,now):
    rows=snapshot.get('gmgn_alerts',[])[:signals.DISPLAY_LIMIT];content=[]
    for row in rows:
        t=row['token'];holders=f'{t["holder_count"]:,.0f}' if t.get('holder_count') is not None else '—'
        cap=row.get('current_market_cap');cap_time=row.get('market_cap_observed')
        cap_tip=('市值更新 '+clock(cap_time)+' · 正常每5分钟同步'+(' · 更新延迟，保留上次值' if row.get('market_cap_stale') else '')) if cap_time else '历史市值，等待首次同步'
        cap_label='市值' if cap is not None else '历史市值'
        if cap is None:cap=t.get('market_cap')
        stats=f'<span>5m成交 <b>{usd(t.get("volume"))}</b></span><span title="{esc(cap_tip)}">{cap_label} <b>{usd(cap)}</b></span>'
        times=f'<time>报警 {clock(row["triggered"])}</time><span>持有人 {holders}'
        if t.get('renowned_count') is not None:times+=f' · KOL {t["renowned_count"]:g}'
        if t.get('smart_degen_count') is not None:times+=f' · 聪明钱 {t["smart_degen_count"]:g}'
        times+='</span>'
        content.append(entry(t,stats,times,event_key=str(row['id'])))
    body=''.join(content) or '<div class="signal-empty"><strong>暂无 GMGN 警报</strong>符合当前规则的代币会显示在这里。</div>'
    rules=snapshot.get('rules',{})
    tip=f'今日毕业 5m > {usd(rules.get("graduate_volume",200000))}；非发射台 5m > {usd(rules.get("other_volume",1000000))}；KOL或聪明钱 ≥ {rules.get("participant_min",1)}；排除币股；每天最多一次'
    stamps=[v.get('updated',0) for v in snapshot.get('gmgn_health',{}).get('chains',{}).values()]
    updated=min(stamps) if len(stamps)==4 and all(stamps) else 0
    return ('<section class="signal-panel" data-signal-kind="gmgn"><div class="rank-title"><h2>GMGN 警报</h2>'
            f'<span title="{esc(tip)}">{esc(gmgn_status(snapshot,now))} · 最近{len(rows)}条 / 最多{signals.DISPLAY_LIMIT}条 · 上下滚动<br>采集 {clock(updated)[6:] if updated else "—"}</span></div><div class="signal-list" tabindex="0" aria-label="GMGN警报列表">'+body+'</div></section>')


def web3_card(snapshot,now):
    rows=snapshot.get('web3_signals',[])[:signals.DISPLAY_LIMIT];health=snapshot.get('web3_health',{});content=[]
    enabled=snapshot.get('web3_enabled',True)
    for row in rows:
        current=enabled and signals.signal_is_current(row,health,now)
        status={'active':'有效','valid':'有效','timeout':'已过期','completed':'已结束','exitRate':'已退出'}.get(row['status'],'历史信号')
        if not current and status=='有效':status='更新延迟'
        direction='买入' if row['direction']=='buy' else '卖出'
        badge=f'<span class="signal-badge {"inactive" if not current else "sell" if row["direction"]=="sell" else ""}">{direction} · {status}</span>'
        stats=f'<span>市值 <b>{usd(row.get("market_cap"))}</b></span><span>触发人数 <b>{row["smart_money_count"]:g}</b></span>' if row.get('smart_money_count') is not None else f'<span>市值 <b>{usd(row.get("market_cap"))}</b></span>'
        times=f'<time>信号 {clock(row["triggered"])}</time>'
        if row.get('exit_rate') is not None:times+=f'<span>退出率 {row["exit_rate"]:g}%</span>'
        content.append(entry(row,stats,times,badge,not current,event_key=row['chain']+':'+row['signal_id']))
    bad=[gmgn.CHAIN_LABELS[c] for c in signals.CHAINS if health.get('chains',{}).get(c,{}).get('error') or not 0<=now-health.get('chains',{}).get(c,{}).get('updated',0)<=signals.WEB3_FRESH]
    status='已暂停' if not enabled else '/'.join(bad)+' 更新延迟' if bad else '每分钟 · SOL / BSC'
    coverage_limited=[c for c in signals.CHAINS if health.get('chains',{}).get(c,{}).get('coverage_status')=='pagination_no_progress']
    if enabled and not bad and coverage_limited:status='来源覆盖有限 · '+ '/'.join(gmgn.CHAIN_LABELS[c] for c in coverage_limited)
    body=''.join(content) or '<div class="signal-empty"><strong>等待币安钱包信号</strong>公开信号当前支持 SOL 和 BSC；不包含个人自定义策略信号。</div>'
    active=sum(enabled and signals.signal_is_current(r,health,now) for r in rows)
    stamps=[health.get('chains',{}).get(c,{}).get('updated',0) for c in signals.CHAINS]
    updated=min(stamps) if all(stamps) else 0
    return ('<section class="signal-panel wallet-panel" data-signal-kind="web3"><div class="rank-title"><h2>Web3 钱包信号</h2>'
            f'<span>有效 {active} · {esc(status)} · 最多{signals.DISPLAY_LIMIT}条<br>采集 {clock(updated)[6:] if updated else "—"}</span></div><div class="signal-list" tabindex="0" aria-label="币安钱包信号列表">'+body+'</div></section>')


def special_card(snapshot):
    content=[]
    for row in snapshot.get('special_alerts',[])[:signals.DISPLAY_LIMIT]:
        tok=row['token'];signal=row['signal'];direction='买入' if signal['direction']=='buy' else '卖出'
        stats=f'<span>GMGN记录5m成交 <b>{usd(tok.get("volume"))}</b></span><span title="原GMGN记录中的历史市值，不是实时市值">记录市值 <b>{usd(tok.get("market_cap"))}</b></span>'
        times=f'<time>命中 {clock(row["triggered"])}</time><span>Web3 {direction} {clock(signal["triggered"])}</span>'
        origin=gmgn.number(row.get('gmgn_triggered'))
        history=bool(origin is not None and datetime.fromtimestamp(origin,gmgn.ZONE).date()!=datetime.fromtimestamp(row['triggered'],gmgn.ZONE).date())
        if origin is not None:
            times+=f'<time>GMGN原报 {clock(origin)}</time>'
            if row.get('watch_expires_at'):times+=f'<span>跟踪到期 {clock(row["watch_expires_at"])}</span>'
        badge='历史GMGN匹配' if history else '双源命中'
        tip='原GMGN报警在10日跟踪池内，与已采集的有效Web3事件匹配；不要求GMGN今天再报，50条展示上限不影响匹配。'
        content.append(entry(tok,stats,times,f'<span class="signal-badge" title="{esc(tip)}">{badge}</span>',event_key=str(row['id'])))
    pool=snapshot.get('match_mode')=='alarm_pool_10d' or snapshot.get('rules',{}).get('match_mode')=='alarm_pool_10d'
    description=('GMGN 报警进入10日跟踪池后，与同链同CA已采集的有效Web3事件双向匹配；命中后保留，不因成交量回落取消。'
                 if pool else 'GMGN 放量条件与同链同合约的有效 Web3 信号同时命中后显示。')
    body=''.join(content) or '<div class="signal-empty"><strong>暂无特级警报</strong>'+description+'</div>'
    cadence='10日池 · 双向核对' if pool else '每分钟'
    checked=max(snapshot.get('generated_at',0),snapshot.get('special_updated_at',0))
    return '<section class="signal-panel special-panel" data-signal-kind="special"><div class="rank-title"><h2>特级警报</h2><span>GMGN × Web3 · '+cadence+f' · 最多{signals.DISPLAY_LIMIT}条<br>核对 '+clock(checked)[6:]+'</span></div><div class="special-list" tabindex="0" aria-label="特级警报列表">'+body+'</div></section>'


def panels(now=None,snapshot=None):
    now=time.time() if now is None else now
    snapshot=signals.dashboard() if snapshot is None else snapshot
    generated=snapshot.get('generated_at',0)
    warning=('<div class="signal-empty">信号采集更新延迟，保留历史记录。</div>' if not 0<=now-generated<=signals.PUBLISH_SECONDS+30 else '')
    return (f'<div class="signal-dashboard" data-refresh="{generated}">'+warning
            +'<div class="signal-panels">'+special_card(snapshot)+gmgn_card(snapshot,now)+web3_card(snapshot,now)+'</div></div>')


def preserve_scroll():
    with st.container(key='signal-scroll-memory'):
        st.html(Path(__file__).with_name('signal_scroll.html').read_text(encoding='utf-8'),unsafe_allow_javascript=True)
