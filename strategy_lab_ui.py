"""A separate, disposable strategy workspace with explicit manual data reads."""
import html
import json
import time
from datetime import datetime,timedelta,timezone
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import store
import strategy_lab as lab
import tradingview_chart as tv
from collector import PublicClient

ZONE=timezone(timedelta(hours=8))
STYLE='''<style>
.lab-header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:5px}
.lab-header h1{font-size:1.8rem!important;padding:0;margin:0}
.lab-badge{color:#edc78f;border:1px solid #725f40;background:#29241b;border-radius:20px;padding:3px 10px;font-size:11px}
.lab-note{color:#8da1ba;font-size:13px;margin-bottom:18px}
.lab-flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 16px}
.lab-step{padding:12px;border:1px solid #2b4055;background:#111c29;border-radius:9px;font-size:12px;color:#9cafc4}
.lab-step b{display:block;color:#dfebf7;font-size:14px;margin-bottom:5px}
.lab-empty{height:300px;display:grid;place-content:center;text-align:center;border:1px dashed #32465d;border-radius:10px;background:#0e1823;color:#91a9bf;line-height:2}
.lab-empty b{font-size:20px;color:#d0e0ee}
.lab-regime{display:flex;gap:8px 20px;align-items:center;flex-wrap:wrap;padding:12px 16px;border:1px solid #30475d;border-radius:9px;background:#111c29;margin:10px 0}
.lab-regime b{color:#b4ed78}.lab-regime span{font-size:12px;color:#9aacc1}
@media(max-width:700px){.lab-flow{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>'''


@st.cache_data(ttl=300,show_spinner=False)
def local_ai_status():
    return lab.codex_readiness()


def number(value,precision=2):
    return '—' if value is None else f'{value:,.{precision}f}'


def chart(result,overlay):
    points=result['points'][-180:]
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.03,row_heights=[.8,.2])
    dates=[datetime.fromtimestamp(p['time']/1000,ZONE) for p in points]
    fig.add_trace(go.Candlestick(x=dates,open=[p['open'] for p in points],high=[p['high'] for p in points],low=[p['low'] for p in points],close=[p['close'] for p in points],name='已收盘K线',increasing_line_color='#b4ed78',decreasing_line_color='#fc8997'),row=1,col=1)
    lines=[('ema20','EMA20','#72b5ee'),('ema50','EMA50','#d2b37b')] if overlay=='趋势均线' else [('band_upper','布林上轨','#8e91df'),('band_mid','布林中轨','#8699ae'),('band_lower','布林下轨','#8e91df')]
    for field,name,color in lines:
        fig.add_trace(go.Scatter(x=dates,y=[p[field] for p in points],name=name,line=dict(color=color,width=1.4)),row=1,col=1)
    fig.add_trace(go.Bar(x=dates,y=[p['quote_volume'] for p in points],name='成交额 USDT',marker_color=['#64924f' if p['close']>=p['open'] else '#975563' for p in points]),row=2,col=1)
    fig.update_layout(height=410,margin=dict(l=5,r=5,t=10,b=0),paper_bgcolor='#0b1017',plot_bgcolor='#0e1823',font_color='#aebed0',xaxis_rangeslider_visible=False,legend=dict(orientation='h',y=1.08),hovermode='x unified')
    fig.update_xaxes(gridcolor='#1e2b3a'); fig.update_yaxes(gridcolor='#1e2b3a',side='right')
    st.plotly_chart(fig,width='stretch',config={'displayModeBar':True,'scrollZoom':True,'displaylogo':False},key='lab_chart')


def read_market(symbols,interval):
    blocked=store.get('monitor_health',{}).get('blocked_until',0)
    if blocked>time.time():
        st.warning('币安采集处于限流等待，本测试页稍后再读取。')
        return
    client=PublicClient()
    results={}; failures=[]
    with st.spinner('读取所选合约K线，仅保存在本次会话…'):
        for symbol in symbols:
            try:
                results[symbol]=dict(bars=lab.fetch_candles(symbol,interval,client),fetched=time.time(),interval=interval)
            except Exception:
                failures.append(symbol)
                if client.blocked_until>time.time(): break
    if results:
        st.session_state['lab_data']=results
        st.session_state['lab_interval_loaded']=interval
    if failures: st.warning('这些标的本次未成功读取：'+ '、'.join(failures)+'。请核对各图表的读取时间；未写入原监控。')


def chat_area(symbol,interval,data,result):
    st.markdown('**与这段K线对话**')
    mode=st.selectbox('分析方式',['当前Codex对话 · 导出分析包','本机Codex桥接 · 待接通','独立API · 待接通'],label_visibility='collapsed',key='lab_ai_mode')
    if mode.startswith('本机'):
        st.caption(local_ai_status())
    elif mode.startswith('独立'):
        st.caption('复用“设置与调试”的AI接口配置。此测试页尚未启用模型调用。')
    else:
        st.caption('先记录问题并导出分析包，回到当前对话交给我分析；无需另配API。')
    questions=st.session_state.setdefault('lab_questions',[])
    with st.container(height=185,border=True):
        if not questions:
            st.caption('可以问：这段行情更适合趋势还是震荡？突破要等什么确认？怎样把我的指标转成规则？')
        for q in questions[-8:]:
            st.caption(q['symbol']+' · '+q['interval']+' · '+datetime.fromtimestamp(q['time'],ZONE).strftime('%H:%M:%S'))
            st.write(q['question'])
    with st.form('lab_question_form',clear_on_submit=True):
        question=st.text_area('你的问题',height=85,placeholder='结合当前K线，帮我梳理趋势策略的入场、退出和失效条件。',key='lab_question_text')
        if st.form_submit_button('加入本次分析',disabled=result is None):
            if question.strip():
                questions.append(dict(symbol=symbol,interval=interval,time=time.time(),question=question.strip()[:8000]))
                st.rerun()
    st.caption('当前未启用AI自动回复，记录问题不产生模型调用。')
    if result is not None:
        packet=lab.analysis_packet(symbol,interval,data['bars'],result,[q for q in questions if q['symbol']==symbol and q['interval']==interval],
            st.session_state.get('lab_draft'),st.session_state.get('lab_tv'))
        st.download_button('下载K线分析包 JSON',json.dumps(packet,ensure_ascii=False,indent=2),file_name=f'{symbol}-{interval}-analysis.json',mime='application/json',width='stretch')


def rule_editor():
    st.markdown('**候选警报参数**')
    with st.form('lab_rule_form'):
        a,b,c,d=st.columns(4)
        cfg=st.session_state.get('lab_rules',lab.DEFAULT_RULES)
        trend=a.number_input('趋势 ADX ≥',min_value=1.,max_value=100.,value=float(cfg['trend_adx']))
        ranges=b.number_input('震荡 ADX <',min_value=0.,max_value=99.,value=float(cfg['range_adx']))
        volume=c.number_input('突破放量倍数 ≥',min_value=0.,max_value=20.,value=float(cfg['volume_multiple']),step=.1)
        confirm=d.number_input('连续收盘确认根数',min_value=1,max_value=5,value=int(cfg['confirm_bars']))
        if st.form_submit_button('应用到本次试算'):
            if ranges>=trend: st.error('震荡ADX阈值应低于趋势ADX阈值。')
            else:
                st.session_state['lab_rules']=dict(trend_adx=trend,range_adx=ranges,volume_multiple=volume,confirm_bars=confirm)
                st.rerun()
    st.caption('趋势模板：EMA20/50方向 + ADX + 收盘突破此前20根高低点 + 放量。震荡模板：低ADX + 布林带外沿 + RSI。只用已收盘K线，过渡、缺口、过期和预热不足时不报警。')


def draft_editor():
    st.markdown('**策略草案 · 尚未回测**')
    st.caption('这里是可编辑模板；AI后续也只先写到草案。验证通过后，再考虑加入正式监控。')
    previous=st.session_state.get('lab_draft',{})
    with st.form('lab_draft_form'):
        regime=st.selectbox('适用环境',['趋势','震荡'],index=0 if previous.get('regime','趋势')=='趋势' else 1)
        entry=st.text_area('入场观察与确认',value=previous.get('entry','等待环境连续确认；趋势看收盘突破与放量，震荡看区间边界与反转确认。'))
        exit_rule=st.text_area('退出与失效条件',value=previous.get('exit','记录结构失效价；趋势转入震荡或波动突破原区间时暂停原规则。'))
        risk=st.text_area('风险与回测要求',value=previous.get('risk','待明确止损、仓位上限、手续费、滑点、资金费率和样本外区间；暂不自动执行。'))
        if st.form_submit_button('暂存草案到本次会话'):
            st.session_state['lab_draft']=dict(regime=regime,entry=entry,exit=exit_rule,risk=risk,backtest_status='未回测')
            st.success('已暂存到本次会话。')


def tradingview_import():
    st.markdown('**把你的指标接进来**')
    a,b=st.columns(2)
    with a:
        st.markdown('**① 指标数值 CSV · 可试导入**')
        st.caption('在TradingView导出图表数据，包含时间、OHLC及可导出的指标列；导入后附加到分析包，本版先不自动叠加图表。')
        symbol=st.text_input('CSV对应合约',value=st.session_state.get('lab_focus','BTCUSDT'),key='lab_tv_symbol')
        period=st.selectbox('CSV对应周期',list(lab.INTERVALS),index=list(lab.INTERVALS).index(st.session_state.get('lab_interval_loaded','15m')))
        zone=st.selectbox('CSV没有时区时按',['UTC','北京时间'])
        file=st.file_uploader('导入 TradingView CSV',type=['csv'],key='lab_tv_file')
        if st.button('载入CSV到本次分析',disabled=file is None):
            try:
                parsed=lab.parse_tv_csv(file.getvalue(),timezone.utc if zone=='UTC' else ZONE)
                st.session_state['lab_tv_csv']=parsed
                st.session_state['lab_tv']=dict(symbol=symbol.strip().upper(),interval=period,alignment='未自动校验时间对齐，未参与信号计算',naive_timezone=zone,
                    columns=parsed['columns'],rows=parsed['rows'][-200:],total_rows=parsed['row_count'],pine_source=st.session_state.get('lab_pine',''))
                st.success(f'已导入{parsed["row_count"]}行，最近200行随分析包导出。')
            except ValueError as error: st.error(str(error))
        parsed=st.session_state.get('lab_tv_csv')
        if parsed: st.dataframe(parsed['rows'][-8:],hide_index=True,width='stretch')
    with b:
        st.markdown('**② Pine源码 · 留给策略转换**')
        st.caption('粘贴你有权查看的源码，后续转换并核对Python计算。当前只记录文本，不执行Pine。受保护/邀请制指标不能直接取得源码。')
        source=st.text_area('Pine Script源码',height=185,key='lab_pine_input')
        if st.button('附加源码到本次分析'):
            if len(source)>50000: st.error('测试版源码请控制在50000字符以内。')
            else:
                st.session_state['lab_pine']=source
                st.session_state['lab_tv']=st.session_state.get('lab_tv',{})|dict(pine_source=source)
                st.success('已附加，仅作为待分析文本。')
    st.markdown('**③ TradingView Webhook · 预留接收层**')
    st.caption('后续让TradingView运行原指标，触发时推送信号；不等于完整指标历史。本地127.0.0.1不能接收云端回调，需单独部署可访问的HTTPS入口。本版未启动接收服务。')
    st.code(json.dumps(dict(source='tradingview',symbol='{{ticker}}',interval='{{interval}}',time='{{time}}',close='{{close}}',signal='trend_or_range_watch'),indent=2),language='json')
    st.markdown('[导出图表与指标数据](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/) · [脚本可见性](https://www.tradingview.com/support/solutions/43000482573-what-are-the-different-types-of-published-scripts/) · [Webhook说明](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)')


def framework():
    st.markdown('**数据与分析框架**')
    st.markdown('在线读取K线 → 临时缓存 → 收盘校验 → 趋势／震荡识别 → 规则候选 → K线对话 → 策略草案 → 历史回测 → 样本外验证 → 再考虑正式监控。')
    st.table([
        {'模块':'K线数据','本版':'TradingView实时图表；分析手动读取币安公开接口，最多6个合约 × 499根','下一步':'增量更新、独立缓存、回测数据快照'},
        {'模块':'趋势与震荡','本版':'EMA20/50、ADX、ATR、RSI、布林带、突破与成交确认','下一步':'多周期确认、历史信号回放、误报统计'},
        {'模块':'AI对话','本版':'记录问题、导出分析包；本机登录状态检查','下一步':'接通本机Codex或独立API，保留多轮上下文'},
        {'模块':'TradingView','本版':'中文永续合约图表、画线/内置指标、CSV试导入、Pine文本附加','下一步':'时间对齐、授权信号接收'},
        {'模块':'策略验证','本版':'可编辑草案，明确未回测','下一步':'手续费/滑点/资金费率、样本外与滚动验证'}])
    st.markdown('**OpenAI怎么接**')
    st.write('当前对话：把分析包交给我即可继续分析，不需另外配置API。网页不会自动把问题发送到当前聊天。')
    st.write('本机Codex桥接：官方App Server可供自定义客户端接入，Codex支持ChatGPT登录。本机已确认该登录方式；仍需开发并验证桥接会话，不能把“已登录”当成模型调用已成功。')
    st.caption(local_ai_status())
    st.write('独立API：网页后端持有服务商API Key，适合后续独立运行或服务器部署；可复用现有接口设置。当前页不发送AI请求。')
    st.markdown('[Codex认证方式](https://learn.chatgpt.com/docs/auth) · [Codex App Server](https://learn.chatgpt.com/docs/app-server) · [OpenAI API接入](https://developers.openai.com/api/docs/quickstart)')


def render_lab():
    st.markdown(STYLE,unsafe_allow_html=True)
    st.markdown('<div class="lab-header"><h1>策略实验室</h1><span class="lab-badge">测试工作区</span></div><div class="lab-note">趋势量化 · 震荡量化 · K线对话。数据、问题和草案仅保留在本次会话。</div>',unsafe_allow_html=True)
    cfg=store.settings()
    options=sorted(set(store.get('exchange_info',{}).get('symbols',[]))|set(cfg['top_symbols']))
    a,b,c=st.columns([3,1,1])
    selected=a.multiselect('选择测试监控代币',options,default=cfg['top_symbols'][:3],max_selections=6,key='lab_symbols')
    interval=b.selectbox('分析 / 图表初始周期',list(lab.INTERVALS),index=1,key='lab_interval')
    c.markdown('<div style="height:27px"></div>',unsafe_allow_html=True)
    update=c.button('更新分析数据',type='primary',width='stretch')
    if update:
        if not selected: st.info('请先选择至少一个合约。')
        else: read_market(selected,interval)
    st.caption('TradingView实时看图 · 分析数据按需更新 · 仅已收盘K线参与试算 · 测试警报不进入原监控')
    tabs=st.tabs(['K线与候选警报','策略草案','TradingView指标','框架与AI接入'])
    datasets=st.session_state.get('lab_data',{})
    rules=st.session_state.get('lab_rules',lab.DEFAULT_RULES)
    with tabs[0]:
        left,right=st.columns([3,1.1],gap='medium')
        focus=None; result=None; data=None
        with left:
            if selected:
                names=selected
                if st.session_state.get('lab_focus') not in names: st.session_state['lab_focus']=names[0]
                a,b,c=st.columns([1.3,1.8,1.3],vertical_alignment='bottom')
                focus=a.selectbox('当前图表 / 分析代币',names,key='lab_focus')
                source=b.radio('图表来源',['TradingView','本地核对图'],horizontal=True,key='lab_chart_source')
                c.link_button('TradingView完整版 ↗',tv.chart_url(focus,interval),width='stretch')
                candidate=datasets.get(focus)
                # A chart selection must never attach another interval's snapshot to the AI packet.
                if candidate and candidate['interval']==interval:
                    data=candidate
                    result=lab.analyze(data['bars'],interval,rules)
                if source=='TradingView':
                    st.iframe(tv.widget_html(focus,interval),height=590,alt=f'{focus} 永续合约 TradingView实时图表')
                    st.caption('图内可缩放、画线、加内置指标；图内切换周期只影响看图，试算和对话以上方周期为准。')
                elif result is not None:
                    overlay=st.radio('本地指标叠加',['趋势均线','震荡区间'],horizontal=True,key='lab_overlay')
                    chart(result,overlay)
                    st.caption('本地核对图使用下方读取时间的已收盘K线快照，不是实时行情。')
                else:
                    st.info('点击“更新分析数据”读取当前周期，便可显示本地核对图。')
                with st.expander('使用自己的TradingView指标 / 图表没有加载？'):
                    st.caption('嵌入图不支持直接加载你的Pine脚本或账号内的指标布局。用上方“TradingView完整版”打开同一合约，可在自己的账号中使用原指标；数据导入仍在“TradingView指标”页。')
                    st.caption('图表由浏览器连接TradingView，无需API Key。若该合约尚未被TradingView收录或网络不可达，可更新分析数据后切换“本地核对图”。')
            else:
                st.info('选择至少一个测试监控代币，即可打开TradingView图表。')
            if result is not None:
                last=result['latest'] or {}
                st.markdown(f'<div class="lab-regime"><b>{html.escape(result["regime"])}</b><span>{html.escape(focus)} · {data["interval"]}</span><span>读取于 {datetime.fromtimestamp(data["fetched"],ZONE):%H:%M:%S}</span><span>{result["closed_count"]} 根已收盘</span></div>',unsafe_allow_html=True)
                cols=st.columns(4)
                for column,label,value in zip(cols,['ADX14','RSI14','ATR14','本根成交额 / 前20根均值'],[last.get('adx'),last.get('rsi'),last.get('atr'),last.get('volume_ratio')]):
                    column.metric(label,number(value,4 if label=='ATR14' else 2))
                if result['signals']: st.warning('本次候选警报：'+'、'.join(result['signals']))
                else: st.info('本次没有候选警报 · '+result['reason'])
                summaries=[]
                for symbol in selected:
                    item=datasets.get(symbol)
                    if not item or item['interval']!=interval: continue
                    r=lab.analyze(item['bars'],item['interval'],rules)
                    summaries.append(dict(代币=symbol,周期=item['interval'],环境=r['regime'],候选='、'.join(r['signals']) or '等待',数据状态=r['reason']))
                st.dataframe(summaries,hide_index=True,width='stretch')
            else:
                st.info('图表可直接查看。需要趋势 / 震荡试算或K线对话时，点击上方“更新分析数据”读取当前合约与周期。')
        with right: chat_area(focus,data['interval'] if data else interval,data,result)
        with st.expander('调整趋势 / 震荡规则',expanded=False): rule_editor()
    with tabs[1]: draft_editor()
    with tabs[2]: tradingview_import()
    with tabs[3]: framework()
    if st.button('清空本次实验',key='lab_reset'):
        for key in list(st.session_state):
            if key.startswith('lab_'): del st.session_state[key]
        st.rerun()
