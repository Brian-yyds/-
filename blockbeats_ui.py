"""Manual reading of cached news; browser refreshes never call the API."""
import json
from pathlib import Path
import streamlit as st
import store
import blockbeats_monitor as news


def frame_html():
    from copy_feed import get_monitor, ui_snapshot
    cfg=news.configuration();health=store.get('blockbeats_health',{})
    payload=dict(items=news.recent(100),poll_seconds=news.INTERVAL,enabled=bool(cfg['enabled']),
        last_success=health.get('last_success'),status=health.get('status',''),
        error=bool(health.get('error')),coverage_limited=bool(health.get('coverage_limited')),
        copy=ui_snapshot(get_monitor().snapshot()))
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    return (Path(__file__).with_name('news_feed.html').read_text(encoding='utf-8')).replace('__NEWS_DATA__',encoded)


@st.fragment(run_every='15s')
def news_panel():
    st.iframe(frame_html(),height=1000,tab_index=0,alt='快讯与跟单信息流')
