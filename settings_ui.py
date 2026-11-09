"""Configuration and local diagnostics, kept off the monitoring screen."""
import os
import time
import uuid
import streamlit as st
import ai_analysis as ai
import gmgn_credentials
import gmgn_monitor as gmgn
import blockbeats_monitor as news
import blockbeats_credentials
import store


def rules():
    cfg=store.settings()
    from monitor_ui import gmgn_acknowledgement
    st.subheader('15分钟异动')
    st.caption('实时监控24小时USDT成交额排名前20的合约，15分钟内上涨或下跌严格超过5%时产生小警报。包含BTC、币股与大宗；榜单筛选不限制这个前20范围。同一波行情不重复报警，不播放声音。')
    st.subheader('GMGN 四链监控')
    gc=gmgn.configuration()
    with st.form('gmgn_rules'):
        enabled=st.checkbox('启用 SOL / BSC / Base / ROB',value=gc['enabled'])
        a,b=st.columns(2)
        people=a.number_input('KOL或聪明钱人数至少',min_value=1,max_value=100000,value=int(gc.get('participant_min',1)))
        graduate=a.number_input('今日毕业：5分钟成交额大于 USD',min_value=1000.,value=float(gc['graduate_volume']),step=50000.)
        other=b.number_input('非发射台：5分钟成交额大于 USD',min_value=1000.,value=float(gc['other_volume']),step=100000.)
        poll=st.number_input('GMGN扫描周期（秒）',min_value=60,max_value=3600,value=max(60,int(gc['poll_seconds'])),step=60)
        st.caption('今日毕业5分钟成交额严格超过200K美元；非发射台仍严格超过100万美元且不限日期。两类都要求KOL至少1人或聪明钱至少1人，保留币股/ETF排除及去重。')
        st.caption('首次毕业警报要求北京时间当天；已报警毕业代币10日内再次突破沿用成交额和KOL条件，不再要求今日毕业。警报卡片直接显示首次触发时间，精确到秒，使用北京时间。')
        st.caption('同链同CA按北京时间每天最多报警一次；持续超阈值不重复报，成交额连续观测5分钟回落至阈值内后再突破才可复报。人数波动不重置成交额去重。重启与确认不重置去重。')
        st.caption('首次报警起10×24小时内跟踪，重复报警不续期，到期停止该轮跟踪并移出榜单。按真实近1小时美元成交额取前10；缺失/过期数据不伪造零成交。')
        if st.form_submit_button('保存 GMGN 规则',type='primary'):
            store.put('gmgn_config',gc|dict(enabled=enabled,participant_mode='kol_or_smart',participant_min=people,graduate_volume=graduate,
                other_volume=other,poll_seconds=poll))
            st.success('已保存，下一轮扫描生效。')
    gmgn_acknowledgement()
    st.caption('GMGN警报卡片的当前市值每5分钟同步，优先复用已有排行/详情请求；离开榜单的最近3条警报按需补查。市值更新不产生新警报或声音，报警时间保持不变；悬停市值可看更新时间，失败保留上次值。')
    extra=store.get('gmgn_extra_30d_rule',{})
    st.caption('额外规则：非北京时间今天毕业、毕业后30天内，5分钟成交额严格超过300K美元，且KOL严格大于3（至少4人）。不要求涨幅或聪明钱人数。币股排除、每天一次及成交额回落后再突破的去重规则不变；10日跟踪窗口不续期。')
    import web3_signals as wallet_signals
    st.subheader('币安 Web3 钱包信号与特级警报')
    with st.form('wallet_signal_rules'):
        enabled=st.checkbox('启用 SOL / BSC 钱包公开信号',value=bool(wallet_signals.configuration()['enabled']))
        st.caption('每分钟获取币安钱包公开聪明钱/KOL买卖信号，每一边成功更新后立即核对特级警报，另有每分钟兜底。页面每3秒读取缓存，不连接个人钱包或个人自定义策略。')
        st.caption('特级警报按事件池匹配：GMGN真实报警后进入原有10日跟踪池，不因成交额回落取消；已实际采集到的有效Web3事件保留10日，同链同CA双向核对。先后顺序不限，命中记录永久保留。GMGN跟踪起点不因重复报警续期，每日同币最多一次，同一Web3信号不重复；声音仍只用于GMGN新警报。')
        if st.form_submit_button('保存钱包信号设置'):
            store.put('web3_signal_config',dict(enabled=enabled))
            st.success('已保存，下一轮自动应用。')
    st.subheader('律动快讯')
    nc=news.configuration()
    with st.form('blockbeats_rules'):
        enabled=st.checkbox('启用律动中文快讯',value=bool(nc['enabled']))
        st.caption('快讯固定为手动阅读。后台每15分钟获取，按31天连续运行约2,976次首屏请求；为其他AI保留额度，分页及失败重试另计，每次API请求均计入本机近31天9,800次上限。')
        budget=news.request_budget()
        st.caption(f'本机近31天已记录 {budget["attempted"]:,} 次尝试；不含启用计数前及其他客户端的调用。官方接口不提供当前剩余额度，官网需登录核实；这里不是官方余额。')
        if st.form_submit_button('保存快讯设置'):
            store.put('blockbeats_config',dict(enabled=enabled,auto_scroll=False))
            st.success('已保存，快讯保持手动阅读。')


def display_settings():
    cfg=store.settings()
    info=store.get('exchange_info',{})
    options=sorted(set(info.get('symbols',[]))|set(cfg['top_symbols'])|set(cfg['priority']))
    with st.form('focus_settings'):
        st.subheader('顶部重点监控')
        top=st.multiselect('顶部代币（最多8个）',options,default=cfg['top_symbols'],max_selections=8)
        st.caption('按选择顺序展示实时价格与近4小时涨跌幅，自动加入重点标的。点击名称打开币安中文合约页面。')
        priority=st.multiselect('其他重点标的',options,default=[s for s in cfg['priority'] if s not in cfg['top_symbols']])
        view=store.get('monitor_view',{})
        st.subheader('榜单显示')
        a,b=st.columns(2)
        scopes=['全市场','仅重点']; assets=['全部','加密货币','股票','大宗']
        scope=a.radio('榜单范围',scopes,index=scopes.index(view.get('scope','全市场')),horizontal=True)
        asset=b.radio('合约类型',assets,index=assets.index(view.get('asset_scope','全部')) if view.get('asset_scope','全部') in assets else 0,horizontal=True)
        search=b.text_input('榜单搜索',value=view.get('search',''),placeholder='留空显示全部')
        st.caption('显示筛选只影响4小时排行榜；15分钟异动独立监控全市场24小时成交额前20名。')
        if st.form_submit_button('保存重点与显示',type='primary'):
            if not top:
                st.error('请至少选择一个顶部重点标的。')
            else:
                store.put('settings',cfg|dict(top_symbols=top,priority=list(dict.fromkeys(top+priority))))
                store.put('monitor_view',dict(scope=scope,asset_scope=asset,oi_direction='增仓',search=search.strip()))
                st.success('已保存，返回监控面板即可查看。')


def api_settings():
    st.subheader('行情数据接口')
    st.text_input('币安 USDⓈ-M 公开行情接口',value='https://fapi.binance.com',disabled=True)
    st.caption('直接采集币安公开 REST / WebSocket 行情，无需币安 API Key。')
    st.text_input('GMGN 官方只读接口',value='https://openapi.gmgn.ai',disabled=True)
    env=bool(os.environ.get('GMGN_API_KEY','').strip())
    saved=(store.USER/'api_keys.gmgn.dpapi').exists()
    st.caption('GMGN Key：'+('使用环境变量' if env else '已加密保存在本机' if saved else '尚未配置'))
    with st.form('gmgn_api'):
        key=st.text_input('更新 GMGN API Key',type='password',help='留空保留当前密钥；密钥不会显示在监控页面。')
        if st.form_submit_button('保存 GMGN 接口'):
            if key.strip():
                try:
                    gmgn_credentials.save_key(key)
                    st.success('密钥已加密保存；下一次请求自动读取。'+('当前环境变量优先，需修改该环境变量才能切换。' if env else ''))
                except ValueError as error:
                    st.error(str(error))
            else:
                st.info('当前密钥保持不变。')
    st.subheader('律动 BlockBeats 接口')
    st.text_input('律动官方快讯接口',value=news.BASE+'/v1/newsflash',disabled=True)
    st.caption('律动 Key：'+('已配置（本机加密或环境变量）' if blockbeats_credentials.key_available() else '尚未配置'))
    with st.form('blockbeats_api'):
        key=st.text_input('更新律动 API Key',type='password',help='留空保留当前密钥，不会显示已有Key。')
        if st.form_submit_button('保存律动接口'):
            if key.strip():
                try:
                    blockbeats_credentials.save_key(key)
                    store.put('blockbeats_refresh_request',uuid.uuid4().hex)
                    st.success('律动Key已加密保存，后台将验证新密钥。')
                except ValueError as error:st.error(str(error))
            else:st.info('当前律动密钥保持不变。')
    st.subheader('AI 接口配置')
    st.caption('可保存多个服务商的接口供后续分析使用；这里保存配置，不发起 AI 调用。')
    config=ai.configuration()
    profiles=config['profiles']
    selected=st.selectbox('选择 AI 配置',['']+[p['id'] for p in profiles],
                          format_func=lambda ident:next((p.get('name',p['model']) for p in profiles if p['id']==ident),'新增配置'))
    profile=next((p for p in profiles if p['id']==selected),{})
    form_id=selected or 'new'
    with st.form('ai_profile_'+form_id):
        name=st.text_input('配置名称',value=profile.get('name',''),key='ai_name_'+form_id)
        protocol=st.selectbox('接口协议',ai.PROTOCOLS,index=ai.PROTOCOLS.index(profile.get('protocol',ai.PROTOCOLS[0])),key='ai_protocol_'+form_id)
        base=st.text_input('API 基础地址',value=profile.get('base_url',''),placeholder='填写服务商提供的基础地址',key='ai_base_'+form_id)
        model=st.text_input('模型 ID',value=profile.get('model',''),key='ai_model_'+form_id)
        secret=st.text_input('AI API Key',type='password',key='ai_secret_'+form_id,help='已有配置留空保留当前密钥。')
        env_key=st.text_input('或使用环境变量名称',value=profile.get('env_key',''),key='ai_env_'+form_id)
        tokens=st.number_input('最大输出 Token',min_value=128,max_value=32000,value=int(profile.get('max_tokens',1800)),key='ai_tokens_'+form_id)
        fields=['max_tokens','max_completion_tokens']
        token_field=st.selectbox('OpenAI兼容：输出上限字段',fields,index=fields.index(profile.get('token_field','max_tokens')),key='ai_field_'+form_id)
        if profile:
            st.caption('密钥状态：'+('已配置' if ai.key_available(profile) else '尚未配置'))
        primary=st.checkbox('设为默认 AI 接口',value=bool(selected and config['primary']==selected),key='ai_primary_'+form_id)
        if st.form_submit_button('保存 AI 接口'):
            ident=selected or uuid.uuid4().hex
            try:
                ai.save_profile(dict(id=ident,name=name.strip() or model.strip(),protocol=protocol,base_url=base.strip(),
                    model=model.strip(),env_key=env_key.strip(),max_tokens=tokens,token_field=token_field),secret)
                if primary:
                    store.put('ai_config',ai.configuration()|{'primary':ident})
                st.success('AI 配置已保存。')
            except ValueError as error:
                st.error(str(error))


def diagnostics():
    st.subheader('数据采集状态')
    audit=store.get('health_audit',{})
    if audit:
        from datetime import datetime
        stamp=datetime.fromtimestamp(audit['checked_at']).strftime('%m-%d %H:%M:%S')
        st.caption(f'自动巡检：{stamp} · 每小时一次；只修复本启动器拥有的退出或停滞进程。')
        for finding in audit.get('findings',[]):
            st.caption(f"{finding['service']} · {finding['code']}")
    now=time.time()
    labels=[('币安采集','monitor_health','heartbeat'),('币安全市场扫描','market_health','heartbeat'),
            ('币安实时流','stream_health','last_message'),('GMGN 四链扫描','gmgn_market','last_success'),
            ('Web3 钱包信号','web3_signal_health','heartbeat'),('律动快讯','blockbeats_health','last_success')]
    for label,key,field in labels:
        snapshot=store.get(key,{})
        stamp=snapshot.get(field,0)
        text=f'{max(0,now-stamp):.0f} 秒前' if stamp else '尚无成功记录'
        st.write(f'**{label}** · {text}')
    snapshot=store.get('gmgn_market',{})
    if snapshot.get('status'): st.caption('GMGN：'+snapshot['status'])
    for chain in gmgn.CHAINS:
        state=snapshot.get('chains',{}).get(chain,{})
        updated=state.get('updated',0)
        age=f'{max(0,now-updated):.0f} 秒前' if updated else '尚未成功'
        retry=max(0,state.get('retry_at',0)-now)
        st.write(f'**{gmgn.CHAIN_LABELS[chain]}** · 更新 {age} · 候选 {state.get("count",0)} 个'+(' · 覆盖有限' if state.get('capped') else ''))
        if state.get('error'):st.caption(f'{state["error"]}（{state.get("error_code","QUERY_ERROR")}）；连续失败 {state.get("failures",0)} 次，{retry:.0f} 秒后重试。')
    info=store.get('gmgn_info_health',{})
    if info:st.caption(f'最近一轮已报警代币详情：尝试 {info.get("attempted",0)} 个，成功 {info.get("succeeded",0)} 个，未成功 {info.get("failed",0)} 个；未成功的保留原缓存，过期后不参与小时榜。')
    if snapshot.get('info_error'):st.caption('详情更新：'+snapshot['info_error'])
    st.caption('GMGN 的每轮耗时另计，遇到接口限流自动等待；不会因刷新调试页额外发起扫描。')
    extra=store.get('gmgn_extra_30d_rule',{})
    if extra:st.caption('30日毕业额外规则：'+extra.get('status','暂停，待数据验证'))
    st.subheader('GMGN 规则试算')
    st.caption('仅在本地验证输入是否符合当前规则，不写入真实警报，也不请求接口。')
    with st.form('gmgn_simulator'):
        kind=st.radio('代币类型',['今天毕业的发射台代币','非发射台代币','非今天毕业的发射台代币'],horizontal=True)
        volume=st.number_input('试算：5分钟成交额 USD',min_value=0.,value=float(gmgn.configuration()['graduate_volume'])+1.)
        a,b=st.columns(2)
        kol=a.number_input('试算：KOL人数',min_value=0,value=1)
        smart=b.number_input('试算：聪明钱人数',min_value=0,value=0)
        if st.form_submit_button('检查是否触发'):
            direct=kind=='非发射台代币'
            token=dict(volume=volume,renowned_count=kol,smart_degen_count=smart,launch_known=True,
                       launchpad='' if direct else 'pump',launchpad_platform='',launchpad_status='1',
                       open_timestamp=now if kind=='今天毕业的发射台代币' else now-2*86400)
            reason=gmgn.reason(token,gmgn.configuration(),now)
            if reason: st.success('符合警报条件：'+reason)
            else: st.info('不符合当前警报条件。')


def render_settings():
    from sync_ui import render as sync_settings
    st.title('设置与调试')
    tabs=st.tabs(['监控规则','重点与显示','数据与 AI 接口','采集与同步','调试诊断'])
    for tab,render in zip(tabs,[rules,display_settings,api_settings,sync_settings,diagnostics]):
        with tab: render()
