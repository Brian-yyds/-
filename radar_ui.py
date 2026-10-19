"""Monitoring and AI panels, separate from the existing one-hour rankings."""
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
import pandas as pd
import streamlit as st
import ai_analysis as ai
import monitoring
import radar
import store


def clock(t):
    return datetime.fromtimestamp(t,timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')


def signal_board(rows,cfg):
    flows,books=radar.inputs(cfg['watchlist'])
    st.subheader('深度分析池 · 订单流与流动性确认')
    st.caption('这里分析重点/深度池，不限制上面的全市场榜单和独立报警。1小时背景、5分钟主动成交确认、盘口约30秒采样；弱候选安静展示。')
    items=[]
    for r in rows:
        f,b=flows.get(r['symbol'],{}),books.get(r['symbol'],{})
        d=radar.assess(r,f,b,cfg)
        if d['kind']:
            items.append({'币种':('★ ' if r['priority'] else '')+r['symbol'],'线索':d['kind'],'状态':d['level'],
                          '1小时涨跌 %':r['price_change'],'成交额放大':r['quote_ratio'],
                          '5分钟成交方向 %':f.get('imbalance5'),'依据':'；'.join(d['evidence'])})
    if items:
        st.dataframe(pd.DataFrame(items),hide_index=True,width='stretch')
    else:
        ready=sum(f.get('ready',False) for s,f in flows.items() if s in cfg['watchlist'])
        st.caption(f'当前没有满足组合条件的线索。订单流已就绪 {ready}/{len(rows)}；新启动或重连后需连续覆盖5分钟。')
    latest=[e for e in radar.events(12) if time.time()-e['time']<1800]
    if latest:
        st.dataframe(pd.DataFrame([{'确认时间':clock(e['time']),'币种':e['symbol'],'确认事件':e['kind'],
                                   '证据':'；'.join(e['snapshot']['decision']['evidence'])} for e in latest]),hide_index=True,width='stretch')
    st.caption('候选不是启动预测；“确认”表示条件已持续满足，不代表价格将继续沿该方向运行。详情可到“订单流与分析”。')


def noise_controls(cfg):
    st.subheader('低噪音过滤与确认')
    with st.form('radar_noise'):
        quiet=st.checkbox('低噪音模式（推荐）：普通单项命中只在榜单标记，仅组合确认写入事件',value=cfg['quiet_mode'])
        priority=st.checkbox('常规确认事件只关注重点币种',value=cfg['radar_priority_only'])
        a,b,c=st.columns(3)
        amount=a.number_input('雷达最低1小时成交额 USDT',min_value=0.,value=float(cfg['radar_min_quote']),step=100000.)
        confirm=b.number_input('组合条件持续 秒',min_value=10,max_value=300,value=int(cfg['confirm_seconds']))
        release=c.number_input('条件解除后重新武装 秒',min_value=30,max_value=600,value=int(cfg['release_seconds']))
        with st.expander('细调：蓄势、主动成交、砸盘和流动性'):
            a,b,c=st.columns(3)
            ratio=a.number_input('雷达成交额放大 倍',min_value=1.01,value=float(cfg['radar_volume_ratio']))
            oi=b.number_input('蓄势OI增加 %',min_value=.1,value=float(cfg['radar_oi_pct']))
            quiet_pct=c.number_input('蓄势1小时涨跌范围 ±%',min_value=.1,value=float(cfg['radar_quiet_pct']))
            a,b,c=st.columns(3)
            flow=a.number_input('主动成交方向强度 %',min_value=1.,max_value=99.,value=float(cfg['radar_flow_pct']))
            accel=b.number_input('砸盘5分钟加速跌幅 %',min_value=.1,max_value=99.,value=float(cfg['radar_accel_pct']))
            large=c.number_input('大额聚合卖出 USDT',min_value=100.,value=float(cfg['radar_large_quote']),step=1000.)
            a,b,c=st.columns(3)
            drop=a.number_input('买盘深度下降门槛 %',min_value=1.,max_value=99.,value=float(cfg['radar_depth_drop_pct']))
            spread=b.number_input('上行/蓄势最大价差 bps',min_value=.1,value=float(cfg['radar_max_spread_bps']))
            slip=c.number_input('卖出滑点风险 bps',min_value=.1,value=float(cfg['radar_slip_bps']))
            a,b=st.columns(2)
            band=a.number_input('盘口价格带 ±bps',min_value=.1,max_value=500.,value=float(cfg['depth_band_bps']))
            sweep=b.number_input('模拟买卖金额 USDT',min_value=100.,value=float(cfg['sweep_quote']),step=1000.)
            st.caption('1 bps = 0.01%。500档未覆盖整个价格带时，深度只显示下界，不参与失衡或深度下降确认。')
        if st.form_submit_button('保存降噪与确认设置',type='primary'):
            update=dict(quiet_mode=quiet,radar_priority_only=priority,radar_min_quote=amount,confirm_seconds=confirm,release_seconds=release,
                        radar_volume_ratio=ratio,radar_oi_pct=oi,radar_quiet_pct=quiet_pct,radar_flow_pct=flow,radar_accel_pct=accel,
                        radar_large_quote=large,radar_depth_drop_pct=drop,radar_max_spread_bps=spread,radar_slip_bps=slip,
                        depth_band_bps=band,sweep_quote=sweep)
            store.put('settings',store.settings()|update)
            st.rerun()
    with st.expander('这些组合如何判定？'):
        st.markdown('''- **蓄势候选**：价格仍在安静区间 + 1小时放量 + 实时OI增仓 + 5分钟主动买入占优 + 价差达标。只展示，不发事件。
- **上行异动确认**：1小时上涨达标 + 放量 + 主动买入占优 + 完整带内深度和价差达标。
- **抛压异动确认**：1小时下跌或5分钟加速下跌 + 主动卖出占优 + 放量/大额聚合卖出 + 买盘下降、失衡或卖出滑点风险。
- **去重**：连续满足设定秒数后记录一次；条件解除一段时间且满足冷却，才可再次触发。重启保留已触发状态。
- **独立急涨急跌**：完全独立，不经过上述组合、成交额或重点过滤。''')


@st.fragment(run_every='3s')
def analysis_panel():
    cfg=store.settings()
    rows=monitoring.build_rows(cfg)
    st.subheader('订单流、流动性与证据解读')
    selected=st.selectbox('分析币种',cfg['watchlist'],key='analysis_coin')
    row=next(r for r in rows if r['symbol']==selected)
    flows,books=radar.inputs([selected])
    flow,book=flows.get(selected,{}),books.get(selected,{})
    decision=radar.assess(row,flow,book,cfg)
    data=radar.snapshot(row,flow,book,decision,cfg=cfg)
    a,b,c,d=st.columns(4)
    hour=data['flow_1h']
    a.metric('1小时主动成交差 USDT',f"{hour['delta1h']:+,.0f}" if hour['delta1h'] is not None else '待数据')
    b.metric('5分钟主动成交差 USDT',f"{flow['delta5']:+,.0f}" if flow.get('ready') else '连续积累中')
    c.metric('盘口价差 bps',f"{book['spread_bps']:.2f}" if book.get('fresh') else '待新快照')
    d.metric('可见价格带覆盖','完整' if book.get('fresh') and book.get('band_complete') else '不足/待数据')
    st.caption('主动成交差 = 主动买入成交额 − 主动卖出成交额。1小时值来自分钟K线，5分钟值来自连续聚合成交流；不等于资金净流入。')
    st.markdown(radar.local_analysis(data))
    if book:
        st.caption('盘口事件 '+clock(book['event_time']/1000)+' · 本机采样 '+clock(book['sampled_at'])+' · '+book['source'])
    with st.expander('查看原始证据快照（不含API密钥）'):
        st.json(data)
    prompt=ai.SYSTEM+'\n\n请分析以下时间点的证据，不要当成当前行情：\n'+json.dumps(data,ensure_ascii=False,indent=2)
    st.download_button('下载证据包，带回当前对话分析',prompt.encode('utf-8'),'radar-evidence-'+selected+'.txt','text/plain')
    st.caption('你可以把证据包发到这个对话，我会据此分析。网站里的本机解读和外部API分析都有独立标记。')
    profiles=ai.configuration()['profiles']
    if profiles:
        mapping={p['id']:p for p in profiles}
        selected_profile=st.selectbox('使用哪个AI复核',list(mapping),format_func=lambda i:mapping[i]['name'],key='analysis_profile')
        if st.button('调用AI分析当前快照',type='primary'):
            try:
                ident=ai.enqueue(data,selected_profile)
                st.success(f'任务 #{ident} 已提交，下方自动更新。')
            except ValueError as exc: st.error(str(exc))
    else:
        st.info('在“AI接口设置”保存协议、地址、模型和密钥，即可调用外部AI复核。')
    st.subheader('AI分析记录')
    heartbeat=store.get('ai_worker_heartbeat',0)
    if time.time()-heartbeat>20: st.warning('AI处理器未就绪，请检查采集器运行状态。')
    jobs=[j for j in ai.jobs(50) if j['snapshot'].get('row',{}).get('symbol')==selected]
    if not jobs: st.caption('当前币种还没有AI调用记录。')
    for j in jobs[:5]:
        labels={'pending':'排队','running':'分析中','done':'已完成','error':'失败'}
        with st.expander(f"#{j['id']} · {clock(j['created'])} · {j['profile']['name']} / {j['profile']['model']} · {labels[j['status']]}",expanded=j==jobs[0]):
            st.caption('数据快照时间 '+clock(j['snapshot']['captured_at'])+' · '+('自动确认事件' if j['source']=='auto' else '手动调用')+' · AI观点待核验')
            if j['result']:
                st.markdown(j['result'])
                st.caption('接口返回用量：'+json.dumps(j['usage'],ensure_ascii=False))
                st.download_button('下载这份AI分析',j['result'].encode('utf-8'),f"ai-analysis-{j['id']}.txt",key=f"ai_export_{j['id']}")
            if j['error']: st.error(j['error'])


def settings_panel():
    st.subheader('AI接口设置')
    if st.session_state.pop('ai_profile_saved',False):
        st.success('AI配置已保存。可到“订单流与分析”调用当前快照进行测试。')
    st.caption('支持多个配置。兼容协议可接入支持Chat Completions的服务或本机模型；也支持Anthropic与Gemini原生协议。模型ID按服务商实际提供填写。')
    cfg=ai.configuration()
    profiles={p['id']:p for p in cfg['profiles']}
    ident=st.selectbox('编辑配置',['new',*profiles],format_func=lambda i:'＋ 新建AI配置' if i=='new' else profiles[i]['name'])
    if 'new_ai_id' not in st.session_state: st.session_state['new_ai_id']=uuid.uuid4().hex
    current=profiles.get(ident,{'id':st.session_state['new_ai_id'],'name':'我的AI','protocol':'OpenAI兼容','base_url':ai.BASES[0],'model':'','max_tokens':1800,'token_field':'max_tokens'})
    protocol=st.selectbox('接口协议',ai.PROTOCOLS,index=ai.PROTOCOLS.index(current['protocol']),key='protocol_'+ident)
    with st.form('ai_profile_'+ident):
        name=st.text_input('配置名称',value=current['name'])
        base=st.text_input('基础地址（含版本路径，如 /v1）',value=current['base_url'] if current['protocol']==protocol else ai.BASES[ai.PROTOCOLS.index(protocol)])
        model=st.text_input('模型ID',value=current['model'])
        key=st.text_input('API密钥（留空保留已存密钥；本机免密模型可不填）',type='password')
        if ai.key_available(current): st.caption('已有密钥配置。Windows本机保存的密钥已使用系统DPAPI加密。')
        env=st.text_input('或使用环境变量名（可选，优先于密钥保险箱）',value=current.get('env_key',''))
        a,b=st.columns(2)
        cap=a.number_input('每次最大输出token',min_value=256,max_value=16000,value=int(current.get('max_tokens',1800)))
        token_field=b.selectbox('兼容接口输出长度字段',['max_tokens','max_completion_tokens'],index=0 if current.get('token_field','max_tokens')=='max_tokens' else 1)
        if st.form_submit_button('保存AI配置',type='primary'):
            try:
                ai.save_profile(dict(id=current['id'],name=name or '我的AI',protocol=protocol,base_url=base,model=model,max_tokens=cap,token_field=token_field,env_key=env.strip()),key)
                st.session_state['new_ai_id']=uuid.uuid4().hex
                st.session_state['ai_profile_saved']=True
                st.rerun()
            except (ValueError,OSError) as exc: st.error(str(exc))
    st.caption('密钥仅供此电脑后台向你指定的地址发请求，不写入证据包、日志或源码压缩包。修改接口地址/协议时需重新填写密钥。')
    if profiles:
        with st.form('ai_policy'):
            primary=st.selectbox('自动分析使用的主AI',list(profiles),format_func=lambda i:profiles[i]['name'],index=list(profiles).index(cfg['primary']) if cfg['primary'] in profiles else 0)
            auto=st.checkbox('自动调用AI复核新确认的上行/抛压事件（会产生API费用）',value=cfg['auto'])
            a,b=st.columns(2)
            daily=a.number_input('每日最多调用次数（含失败，所有配置共用）',min_value=1,max_value=200,value=int(cfg['daily_limit']))
            cooldown=b.number_input('自动分析同一币种间隔 分钟',min_value=5,max_value=1440,value=int(cfg['auto_cooldown_minutes']))
            if st.form_submit_button('保存调用策略'):
                store.put('ai_config',ai.configuration()|dict(primary=primary,auto=auto,daily_limit=daily,auto_cooldown_minutes=cooldown))
                st.success('已保存。候选观察不会自动调用AI。')
    st.caption('默认手动调用；不自动重试失败请求。网站只做监控与分析，未接入下单功能。')
