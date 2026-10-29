"""Stable audio player + read-only event bridge; no sound from historical backlog."""
import json
from pathlib import Path
import streamlit as st
import store
import monitoring
import gmgn_monitor as gmgn


def snapshot():
    with store.connect() as con:
        gmgn.schema(con)
        latest=dict(binance=con.execute('SELECT COALESCE(MAX(id),0) FROM spike_alerts').fetchone()[0],
                    gmgn=con.execute('SELECT COALESCE(MAX(id),0) FROM gmgn_alerts').fetchone()[0])
        gmgn_rows=con.execute('SELECT id,triggered,data FROM gmgn_alerts ORDER BY id DESC LIMIT 128').fetchall()
    events=[dict(source='binance',id=r[0],time=r[1]) for r in monitoring.monitored_spike_alerts(store.spike_alerts(limit=128))]
    for ident,stamp,raw in gmgn_rows:
        if not gmgn.equities.equity_reason(json.loads(raw)):
            events.append(dict(source='gmgn',id=ident,time=stamp))
    return dict(version=1,latest=latest,events=events)


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
const channel=new BroadcastChannel('perp-workbench-alerts-v1');
const packet='''+payload+''';
function send(){channel.postMessage(packet)}send();setInterval(send,1000);
channel.onmessage=e=>{if(e.data?.requestSnapshot)send()};
</script>''')
    with st.container(key='sound-events'):
        st.iframe(frame,height=1,tab_index=-1,alt='警报事件信号')
