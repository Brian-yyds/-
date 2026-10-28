"""Configuration and local diagnostics, kept off the monitoring screen."""
import os
import time
import uuid
import streamlit as st
import ai_analysis as ai
import gmgn_credentials
import gmgn_monitor as gmgn
import store


def rules():
    cfg=store.settings()
    st.subheader('币安急涨急跌')
    with st.form('spike_thresholds'):
        st.caption('币股、ETF、大宗合约正常参与主监控和警报；仅顶部成交统计排除这些类别。')
        a,b=st.columns(2)
        up=a.number_input('15分钟急涨达到 %',min_value=.1,max_value=1000.,value=float(cfg['spike_up_pct']),step=.5)
        down=b.number_input('15分钟急跌达到 %',min_value=.1,max_value=99.9,value=float(cfg['spike_down_pct']),step=.5)
        if st.form_submit_button('保存币安规则',type='primary'):
            store.put('settings',store.settings()|dict(spike_up_pct=up,spike_down_pct=down,include_stocks=True))
            st.success('已保存，采集服务自动应用。')
    from monitor_ui import acknowledgement,gmgn_acknowledgement
    acknowledgement()
    st.subheader('GMGN 四链监控')
    gc=gmgn.configuration()
    with st.form('gmgn_rules'):
        enabled=st.checkbox('启用 SOL / BSC / Base / ROB',value=gc['enabled'])
        a,b=st.columns(2)
        kol=a.number_input('所有警报：KOL 人数大于',min_value=0,max_value=100000,value=int(gc['kol_count']))
        people=b.number_input('今日毕业：聪明钱 + KOL 人数大于',min_value=0,max_value=100000,value=int(gc['people']))
        graduate=a.number_input('今日毕业：5分钟成交额大于 USD',min_value=1000.,value=float(gc['graduate_volume']),step=50000.)
        other=b.number_input('非发射台：5分钟成交额大于 USD',min_value=1000.,value=float(gc['other_volume']),step=100000.)
        days=a.number_input('成交榜首：创建不超过天数',min_value=1,max_value=3650,value=int(gc['leader_days']))
        cap=b.number_input('成交榜首：市值上限 USD',min_value=1.,max_value=1e12,value=float(gc['leader_cap']),step=1000000.)
        poll=st.number_input('每轮扫描后的最短间隔（秒）',min_value=15,max_value=3600,value=int(gc['poll_seconds']),step=5)
        st.caption('“大于”不包含等于。毕业按北京时间当天；两类警报都要求 KOL 超过阈值。成交榜首的天数、市值过滤不改变警报规则。')
        st.caption('GMGN警报排除币股/股票及ETF代币：按发行方合约目录、证券平台标记及发行方名称识别；不按代币简称或整条链屏蔽。热搜、成交榜首和币安采集仍按原范围。')
        st.caption('热搜按你在GMGN页面的排序：SOL / BSC / Base / ROB 四链热搜名单，5分钟成交额降序第一。不受成交榜首或警报筛选影响。')
        st.caption('警报持续命中只记录一次；连续观测5分钟不满足条件后才重新触发。右侧展示最近3条历史警报。')
        if st.form_submit_button('保存 GMGN 规则',type='primary'):
            store.put('gmgn_config',gc|dict(enabled=enabled,kol_count=kol,people=people,graduate_volume=graduate,
                other_volume=other,leader_days=days,leader_cap=cap,poll_seconds=poll))
            st.success('已保存，下一轮扫描生效。')
    gmgn_acknowledgement()


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
        scopes=['全市场','仅重点']; assets=['全部','加密货币','股票','大宗']; directions=['增仓','减仓']
        scope=a.radio('榜单范围',scopes,index=scopes.index(view.get('scope','全市场')),horizontal=True)
        asset=b.radio('合约类型',assets,index=assets.index(view.get('asset_scope','全部')) if view.get('asset_scope','全部') in assets else 0,horizontal=True)
        direction=a.radio('OI方向',directions,index=directions.index(view.get('oi_direction','增仓')),horizontal=True)
        search=b.text_input('榜单搜索',value=view.get('search',''),placeholder='留空显示全部')
        st.caption('显示筛选只影响排行榜；急涨急跌警报覆盖加密、币股/ETF和大宗。成交统计单独剔除币股、大宗及ETF。')
        if st.form_submit_button('保存重点与显示',type='primary'):
            if not top:
                st.error('请至少选择一个顶部重点标的。')
            else:
                store.put('settings',cfg|dict(top_symbols=top,priority=list(dict.fromkeys(top+priority))))
                store.put('monitor_view',dict(scope=scope,asset_scope=asset,oi_direction=direction,search=search.strip()))
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
    now=time.time()
    labels=[('币安采集','monitor_health','heartbeat'),('币安全市场扫描','market_health','heartbeat'),
            ('币安实时流','stream_health','last_message'),('GMGN 四链扫描','gmgn_market','last_success'),
            ('15日市场成交统计','market_volume_15d','updated_at')]
    for label,key,field in labels:
        snapshot=store.get(key,{})
        stamp=snapshot.get(field,0)
        text=f'{max(0,now-stamp):.0f} 秒前' if stamp else '尚无成功记录'
        st.write(f'**{label}** · {text}')
    snapshot=store.get('gmgn_market',{})
    if snapshot.get('status'): st.caption('GMGN：'+snapshot['status'])
    st.caption('GMGN 的每轮耗时另计，遇到接口限流自动等待；不会因刷新调试页额外发起扫描。')
    st.subheader('GMGN 规则试算')
    st.caption('仅在本地验证输入是否符合当前规则，不写入真实警报，也不请求接口。')
    with st.form('gmgn_simulator'):
        kind=st.radio('代币类型',['今天毕业的发射台代币','非发射台代币','非今天毕业的发射台代币'],horizontal=True)
        volume=st.number_input('试算：5分钟成交额 USD',min_value=0.,value=float(gmgn.configuration()['graduate_volume'])+1.)
        a,b=st.columns(2)
        smart=a.number_input('试算：聪明钱人数',min_value=0,value=1)
        kol=b.number_input('试算：KOL人数',min_value=0,value=3)
        if st.form_submit_button('检查是否触发'):
            direct=kind=='非发射台代币'
            token=dict(volume=volume,smart_degen_count=smart,renowned_count=kol,launch_known=True,
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
