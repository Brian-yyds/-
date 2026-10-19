"""A single-purpose alarm screen: three rankings, one alarm rule, sidebar controls."""
import html
import time
from datetime import datetime, timedelta, timezone
import streamlit as st
import monitoring
import store

ZONE=timezone(timedelta(hours=8))
STYLE='''<style>
.stApp{background:#0b1017;color:#e6edf5}
[data-testid="stSidebar"]{background:#101923;border-right:1px solid #253142}
[data-testid="stMainBlockContainer"]{padding-top:1.5rem;padding-bottom:1.5rem}
h1,h2,h3{letter-spacing:-.025em}
.screen-heading{display:flex;align-items:baseline;gap:20px;margin:0 0 5px}
.screen-heading h1{font-size:30px;padding:0;margin:0;font-weight:750}
.screen-heading span,.muted{color:#8294a9;font-size:13px}
.live-line{display:flex;align-items:center;gap:12px;color:#9eafc3;font-size:12px;margin:4px 0 16px;flex-wrap:wrap}
.live{color:#a9dc85}.offline{color:#ffbe77}.pending-label{color:#ff858d;margin-left:auto;font-weight:650}
.pending-label.quiet-label{color:#7f9489;font-weight:400}
.rank-card{background:#111b27;border:1px solid #29374a;border-radius:14px;overflow:hidden}
.rank-title{padding:18px 18px 12px;border-bottom:1px solid #263346;display:flex;align-items:baseline;justify-content:space-between}
.rank-title h2{font-size:26px!important;font-weight:750;margin:0;padding:0}.rank-title span{font-size:12px;color:#8fa2ba}
.rank-title h2.oi-accent{color:#83c3ff!important}.rank-title h2.up-accent{color:#9de57c!important}.rank-title h2.down-accent{color:#ff8e99!important}
.oi-accent{color:#83c3ff}.up-accent{color:#9de57c}.down-accent{color:#ff8e99}
.rank-scroll{height:535px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#344457 #111b27}
.rank-table,.alarm-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.rank-table th{position:sticky;top:0;background:#152232;color:#8fa5be;font-size:12px;font-weight:500;z-index:1}
.rank-table th,.rank-table td{padding:11px 14px;text-align:right;border-bottom:1px solid #1f2b3b;white-space:nowrap}
.rank-table td{font-size:14px}.rank-table td:first-child,.rank-table th:first-child{width:30px;color:#6f829b;text-align:left}
.rank-table td:nth-child(2),.rank-table th:nth-child(2){text-align:left}
.coin{font-weight:650;color:#e6edf5}.star{color:#f4d27d}.rank-table td.move{font-size:18px;font-weight:750}
.rank-table tbody tr:hover{background:#182638}.empty{text-align:center;color:#8195af;padding:72px 12px;font-size:14px}
.alarm-heading{font-size:21px;font-weight:700;margin:21px 0 10px}
.alarm-box{border:1px solid #713a44;background:#251820;border-radius:12px;overflow:auto;max-height:350px;scrollbar-width:thin}
.alarm-table th,.alarm-table td{padding:12px 16px;text-align:left;border-bottom:1px solid #49303a;font-size:14px}
.alarm-table th{color:#e6a2ac;font-size:12px;background:#301e28;position:sticky;top:0}.alarm-table td{color:#f2dde2}
.quiet{border:1px solid #243244;background:#0f1924;border-radius:10px;padding:15px 18px;color:#8eaa9c;font-size:13px}
@media(max-width:1100px){.rank-title h2{font-size:21px}.rank-table th,.rank-table td{padding:10px 9px}.move{font-size:15px}}
</style>'''


def amount(value):
    if value is None: return '—'
    if abs(value)>=1e8: return f'{value/1e8:.1f}亿'
    if abs(value)>=1e4: return f'{value/1e4:.0f}万'
    return f'{value:,.0f}'


def clock(ms):
    return datetime.fromtimestamp(ms/1000,ZONE).strftime('%m-%d %H:%M:%S')


def coin(symbol,priority=False):
    return ('<span class="star">★ </span>' if priority else '')+html.escape(symbol.removesuffix('USDT'))


def ranking_card(rows,title,kind):
    oi=kind.startswith('oi')
    accent='oi-accent' if oi else 'up-accent' if kind=='up' else 'down-accent'
    label='近1h增仓' if kind=='oi_up' else '近1h减仓' if kind=='oi_down' else '近1h涨幅' if kind=='up' else '近1h跌幅'
    body=[]
    for index,r in enumerate(rows,1):
        value=r['oi_change'] if oi else r['price_change']
        approximate=oi and r['oi_basis']!='实时采样'
        tip=f"{r['oi_basis']} · 回看{r['oi_span']:.1f}分钟" if oi else f"回看{r['price_span']:.2f}分钟" if r.get('price_span') is not None else ''
        body.append(f'<tr><td>{index:02d}</td><td class="coin">{coin(r["symbol"],r["priority"])}</td>'
                    f'<td class="move {accent}" title="{html.escape(tip,quote=True)}">{"≈" if approximate else ""}{value:+.2f}%</td>'
                    f'<td class="muted">{amount(r["quote"])}</td></tr>')
    content=('<table class="rank-table"><thead><tr><th>#</th><th>币种</th><th>'+label+'</th><th>成交额 U</th></tr></thead><tbody>'+''.join(body)+'</tbody></table>') if body else '<div class="empty">等待有效数据</div>'
    return f'<section class="rank-card"><div class="rank-title"><h2 class="{accent}">{title}</h2><span>1 小时 · TOP 30</span></div><div class="rank-scroll">{content}</div></section>'


def alarm_table(records):
    body=[]
    for r in records:
        ident,stamp,symbol,direction,change,base,price,base_time,event_time,basis,ack=r
        color='up-accent' if direction=='急涨' else 'down-accent'
        body.append(f'<tr><td>{clock(event_time)}</td><td class="coin">{coin(symbol)}</td>'
                    f'<td>{html.escape(direction)}</td><td><strong class="{color}">{change:+.2f}%</strong></td>'
                    f'<td>{price:.8g}</td></tr>')
    return '<div class="alarm-box"><table class="alarm-table"><thead><tr><th>北京时间</th><th>币种</th><th>方向</th><th>15分钟内变动</th><th>触发价</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'


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
    pending=store.spike_alerts(unacknowledged=True,limit=10000)
    if st.button(f'确认当前报警（{len(merged_alarms(pending))}）',disabled=not pending,key='ack_spike_alarms',width='stretch'):
        store.acknowledge_spikes([r[0] for r in pending])
        st.rerun()


@st.fragment(run_every='3s')
def board(scope,search,oi_direction):
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
    pending_records=store.spike_alerts(unacknowledged=True,limit=10000)
    pending=merged_alarms(pending_records)
    summary=(f'{pending[0][2].removesuffix("USDT")} {pending[0][3]} {pending[0][4]:+.2f}% · {len(pending)} 条待确认') if pending else '暂无待确认报警'
    summary_css='pending-label' if pending else 'pending-label quiet-label'
    st.markdown(f'<div class="live-line"><span class="{css}">{state}</span><span>全市场 {len(rows)} 币</span><span>排行 1 小时</span><span>{datetime.now(ZONE):%H:%M:%S}</span><span class="{summary_css}">{html.escape(summary)}</span></div>',unsafe_allow_html=True)
    shown=[r for r in rows if (scope!='仅重点' or r['priority']) and search.upper().strip() in r['symbol']]
    for col,title,kind in zip(st.columns(3,gap='medium'),['OI 异动榜','上涨榜','下跌榜'],['oi_up' if oi_direction=='增仓' else 'oi_down','up','down']):
        with col:
            st.markdown(ranking_card(monitoring.ranked(shown,kind)[:30],title,kind),unsafe_allow_html=True)
    st.markdown('<div class="alarm-heading">15 分钟急涨 / 急跌报警</div>',unsafe_allow_html=True)
    if pending:
        st.markdown(alarm_table(pending[:200]),unsafe_allow_html=True)
        if len(pending)<len(pending_records): st.caption('同币同方向合并显示最新报警。')
        if len(pending)>200: st.caption('显示最新200条待确认报警，其余仍保留待处理。')
    else:
        st.markdown(f'<div class="quiet">已开启全市场监控：15 分钟内急涨 ≥ {cfg["spike_up_pct"]:g}% 或急跌 ≥ {cfg["spike_down_pct"]:g}% 时报警。</div>',unsafe_allow_html=True)
    # Rankings are a viewing filter; they must never hide market-wide alarms.
    if not alive or not scanner_alive:
        st.warning('采集服务正在连接或心跳已过期；恢复连接后自动更新。')


def render():
    st.set_page_config(page_title='全市场异动监控',page_icon='◉',layout='wide')
    st.markdown(STYLE,unsafe_allow_html=True)
    cfg=store.settings()
    with st.sidebar:
        st.markdown('### 监控设置')
        with st.form('spike_thresholds'):
            st.markdown('**15 分钟急变报警**')
            up=st.number_input('急涨达到 %',min_value=.1,max_value=1000.,value=float(cfg['spike_up_pct']),step=.5)
            down=st.number_input('急跌达到 %',min_value=.1,max_value=99.9,value=float(cfg['spike_down_pct']),step=.5)
            if st.form_submit_button('应用报警阈值',type='primary',width='stretch'):
                store.put('settings',store.settings()|dict(spike_up_pct=up,spike_down_pct=down))
                st.rerun()
        acknowledgement()
        st.divider()
        scope=st.radio('榜单范围',['全市场','仅重点'],horizontal=True)
        search=st.text_input('搜索币种',placeholder='输入币名')
        oi_direction=st.radio('OI方向',['增仓','减仓'],horizontal=True)
        with st.expander('编辑重点币种'):
            options=sorted(set(monitoring.market_symbols(cfg))|set(cfg['priority']))
            with st.form('priority'):
                priority=st.multiselect('重点币种',options,default=cfg['priority'])
                if st.form_submit_button('保存重点币种',width='stretch'):
                    store.put('settings',store.settings()|{'priority':priority})
                    st.rerun()
    st.markdown('<div class="screen-heading"><h1>全市场异动监控</h1><span>币安 USDT 永续</span></div>',unsafe_allow_html=True)
    board(scope,search,oi_direction)
