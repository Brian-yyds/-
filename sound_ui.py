"""Stable audio player + read-only event bridge; no sound from historical backlog."""
import json
from pathlib import Path
import streamlit as st
import store
import gmgn_monitor as gmgn


def snapshot():
    with store.connect() as con:
        gmgn.schema(con)
        latest=con.execute('SELECT COALESCE(MAX(id),0) FROM gmgn_alerts').fetchone()[0]
    # Use exactly the same three durable records as the visible GMGN alarm box.
    # Ranking changes, last_seen refreshes and Binance events are not alarms.
    events=[dict(source='gmgn',id=r['id'],time=r['triggered']) for r in gmgn.recent_alerts()]
    return dict(version=2,latest=dict(gmgn=latest),events=events)


def sound_control():
    with st.container(key='sound-control'):
        st.iframe(Path(__file__).with_name('alert_sound.html').read_text(encoding='utf-8'),
                  height=30,tab_index=0,alt='警报声音开关')


@st.fragment(run_every='3s')
def sound_events():
    payload=json.dumps(snapshot(),allow_nan=False,separators=(',',':'))
    # This frame never receives credentials or invokes an API. A stable sibling
    # keeps its AudioContext and user activation while this signal frame updates.
    frame=('''<!doctype html><script>
const channel=new BroadcastChannel('perp-workbench-gmgn-alerts-v2');
const packet='''+payload+''';
function send(){channel.postMessage(packet)}send();setInterval(send,1000);
channel.onmessage=e=>{if(e.data?.requestSnapshot)send()};
</script>''')
    with st.container(key='sound-events'):
        st.iframe(frame,height=1,tab_index=-1,alt='警报事件信号')
