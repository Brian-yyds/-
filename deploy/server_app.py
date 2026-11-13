"""Server-only signal dashboard. This file replaces app.py in the server bundle only."""
import time
import json
from pathlib import Path
import streamlit as st
import store
import gmgn_monitor as gmgn
import web3_signals as wallet
from signal_ui import STYLE,panels,preserve_scroll,clock
from sound_ui import sound_control,sound_events

st.set_page_config(page_title='链上信号监控 · 服务器',page_icon='◉',layout='wide',initial_sidebar_state='collapsed')
st.markdown(STYLE+'''<style>
.stApp{background:#141b23;color:#b8c7d1}
.block-container{padding:18px 24px 12px;max-width:none}
header[data-testid="stHeader"],footer,[data-testid="stToolbar"]{display:none}
h1{font-size:23px!important;color:#a5bebc!important;margin:0;padding:0!important}
.signal-dashboard{container-type:inline-size;container-name:market-panel}
.signal-panels{--feed-body:calc(100dvh - 150px);gap:14px}
.rank-title{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #34464a}
.rank-title h2{margin:0!important;padding:0!important;font-weight:600}
.signal-panel .rank-title{height:52px}
.signal-panel .rank-title h2{font-size:19px!important}
.signal-entry{padding:16px 18px!important}
.signal-token-name b{font-size:19px!important}.signal-numbers b{font-size:25px!important}
.signal-time{font-size:12px!important}.signal-address{font-size:11px!important}
button,summary{color:#a5bebc!important}
[data-testid="stExpander"]{margin-top:5px}
@container market-panel (max-width:760px){.signal-panels{--feed-body:420px}}
</style>''',unsafe_allow_html=True)
title,control=st.columns([7,1],vertical_alignment='center')
with title:st.markdown('<h1>链上信号监控 · 服务器</h1>',unsafe_allow_html=True)
with control:sound_control()
preserve_scroll()


@st.fragment(run_every='3s')
def signals_board():
    snapshot=wallet.dashboard()
    st.markdown(panels(snapshot=snapshot),unsafe_allow_html=True)
    sound_events(snapshot.get('gmgn_alerts',[]))


signals_board()
with st.expander('规则与运行状态'):
    cfg=gmgn.configuration()
    st.caption('仅运行 GMGN、币安 Web3 钱包公开信号和特级匹配；不运行币安合约行情、律动或上传工作进程。')
    st.caption('特级匹配：真实GMGN报警进入10日池，两边先后顺序都支持；后续成交额回落不撤销已收到的资格。')
    try:
        status=json.loads(Path('/opt/perp-workbench/shared/recovery/status.json').read_text())
        problems=[code for rows in status.get('faults',{}).values() for code in rows]
        notes=status.get('notes',[])
        st.caption('独立巡检：'+clock(status.get('checked_at'))+' · 每30秒检查')
        if problems:st.error('监控异常：'+', '.join(problems))
        if not status.get('release',{}).get('ok',False):st.error('运行版本校验异常，请检查部署记录')
        if notes:st.warning('运行提示：'+', '.join(notes))
        if status.get('error'):st.error('独立巡检异常：'+str(status['error']))
        if time.time()-status.get('checked_at',0)>120:st.error('独立巡检已超时')
        maintenance=json.loads(Path('/opt/perp-workbench/shared/maintenance/status.json').read_text())
        st.caption('完整检查：'+clock(maintenance.get('checked_at'))+' · 数据库 '+str(maintenance.get('database_integrity')))
        st.caption('每日备份：'+', '.join(k+' '+str(v.get('status')) for k,v in maintenance.get('backups',{}).items())+' · 保留最近7天')
    except (OSError,ValueError,TypeError):st.caption('完整巡检报告尚未就绪')
    st.caption('每分钟采集与匹配；GMGN市值约每5分钟同步。市值更新不重报，声音只用于新GMGN警报。')
    st.caption(f"今日毕业5m成交额>{cfg['graduate_volume']:,.0f}美元；非发射台>{cfg['other_volume']:,.0f}美元；KOL或聪明钱至少{cfg.get('participant_min',1)}人。另有30天内非今日毕业5m>300K且KOL>3。排除币股；每日一次，原去重与10日跟踪不变。")
    audit=store.get('health_audit',{})
    if audit:st.caption('最近巡检：'+clock(audit['checked_at']))
    with st.form('cloud_enable_sources'):
        gmgn_on=st.checkbox('启用 GMGN 四链',value=cfg['enabled'])
        web3_on=st.checkbox('启用 Web3 SOL / BSC 公开信号',value=wallet.configuration()['enabled'])
        if st.form_submit_button('保存服务器开关'):
            store.put('gmgn_config',cfg|dict(enabled=gmgn_on))
            store.put('web3_signal_config',dict(enabled=web3_on))
            st.success('已保存，仅影响服务器。')
