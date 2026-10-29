"""Cached news in a stable iframe; browser animation never calls the API."""
import json
from pathlib import Path
import streamlit as st
import store
import blockbeats_monitor as news


def frame_html():
    cfg=news.configuration();health=store.get('blockbeats_health',{})
    payload=dict(items=news.recent(100),auto_scroll=bool(cfg['auto_scroll']),
        scroll_speed=max(4,min(40,float(cfg['scroll_speed']))),enabled=bool(cfg['enabled']),
        last_success=health.get('last_success'),status=health.get('status',''),
        error=bool(health.get('error')),coverage_limited=bool(health.get('coverage_limited')))
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    return (Path(__file__).with_name('news_feed.html').read_text(encoding='utf-8')).replace('__NEWS_DATA__',encoded)


@st.fragment(run_every='15s')
def news_panel():
    st.iframe(frame_html(),height=850,tab_index=0,alt='律动快讯自动滚动信息流')
