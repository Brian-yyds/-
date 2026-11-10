"""All alarm history stays scrollable; sound cannot outrun the displayed frame."""
from datetime import datetime
import json
import pytest
import store
import gmgn_monitor as g
import web3_signals as w
import signal_ui
import sound_ui

NOW=datetime(2026,11,9,23,tzinfo=g.ZONE).timestamp()


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'history.sqlite')
    clock=[NOW]
    monkeypatch.setattr(w.time,'time',lambda:clock[0])
    with store.connect() as con:g.schema(con)
    return clock


def seed(count=6):
    with store.connect() as con:
        for i in range(1,count+1):
            token=g.normalize(dict(address='0x'+f'{i:040x}',symbol=f'HISTORY_{i}',name=f'History {i}',
                volume=1000001,renowned_count=1,smart_degen_count=0,market_cap=100000+i,
                holder_count=100,launchpad='',launchpad_platform=''),'base',NOW)
            con.execute('INSERT INTO gmgn_alerts(chain,address,rule,triggered,last_seen,acknowledged,data) VALUES (?,?,?,?,?,?,?)',
                ('base',token['address'],'非发射台放量',NOW-count+i,NOW-count+i,1,json.dumps(token)))


def test_all_history_is_retained_but_display_is_limited_to_hundred(isolated):
    seed(121)
    assert len(g.recent_alerts())==3  # Legacy callers keep their explicit page size.
    assert len(g.recent_alerts(7))==7
    assert len(g.recent_alerts(None))==121
    view=w.dashboard()
    assert len(view['gmgn_alerts'])==100
    assert view['gmgn_alerts'][0]['token']['symbol']=='HISTORY_121'
    assert view['gmgn_alerts'][-1]['token']['symbol']=='HISTORY_22'
    markup=signal_ui.gmgn_card(view,NOW)
    assert markup.count('class="signal-entry"')==100
    assert '最近100条 / 最多100条 · 上下滚动' in markup and 'HISTORY_1<' not in markup


def test_live_alarm_does_not_wait_for_cached_minute_snapshot(isolated):
    cached=dict(generated_at=NOW,gmgn_alerts=[],web3_signals=[dict(sentinel='unchanged')],special_alerts=[],rules={})
    store.put('signal_dashboard',cached)
    seed()
    # No publication is performed after the alarms arrive.
    assert store.get('signal_dashboard')['gmgn_alerts']==[]
    view=w.dashboard()
    assert len(view['gmgn_alerts'])==6
    assert view['generated_at']==NOW and view['web3_signals']==cached['web3_signals']
    assert store.get('signal_dashboard')==cached  # View overlays are read-only.


def test_special_display_top_hundred_does_not_delete_durable_history(isolated):
    seed(1)
    with store.connect() as con:
        w.schema(con);token=g.alarm_history(con)[0]['token']
        for i in range(121):
            payload=dict(token=token,signal=dict(direction='buy',triggered=NOW-i))
            con.execute('INSERT INTO special_alerts(chain,address,day,triggered,data) VALUES (?,?,?,?,?)',
                        ('base',token['address'],str(i),NOW-i,json.dumps(payload)))
    view=w.dashboard()
    assert len(view['special_alerts'])==100
    assert view['special_alerts'][0]['triggered']==NOW
    assert view['special_alerts'][-1]['triggered']==NOW-99
    assert signal_ui.special_card(view).count('class="signal-entry"')==100
    with store.connect() as con:assert con.execute('SELECT COUNT(*) FROM special_alerts').fetchone()[0]==121


def test_publish_also_retains_every_alarm_in_durable_view(isolated):
    seed(9)
    view=w.publish(NOW)
    assert len(view['gmgn_alerts'])==9
    assert len(store.get('signal_dashboard')['gmgn_alerts'])==9


def test_old_acknowledged_history_survives_watch_expiry_and_legacy_cleanup(isolated):
    seed(5)
    with store.connect() as con:
        con.execute('UPDATE gmgn_alerts SET triggered=triggered-?,last_seen=last_seen-?',(40*86400,40*86400))
    g.record_scan('sol',[],g.DEFAULTS,NOW)
    # Existing workers may still execute the old cleanup until their next boot.
    with store.connect() as con:
        con.execute('DELETE FROM gmgn_alerts WHERE acknowledged=1 AND triggered<? AND id NOT IN (SELECT id FROM gmgn_alerts ORDER BY triggered DESC,id DESC LIMIT 3)',(NOW-30*86400,))
        assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==5
    assert len(w.dashboard()['gmgn_alerts'])==5


def test_audio_packet_uses_only_the_frame_already_displayed(isolated,monkeypatch):
    seed(4)
    shown=w.dashboard()['gmgn_alerts']
    # A later database alarm must wait for its own displayed frame.
    with store.connect() as con:
        row=shown[0];token=row['token']|dict(address='0x'+'f'*40,symbol='NOT_YET_SHOWN')
        con.execute('INSERT INTO gmgn_alerts(id,chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?,?)',
            (99,'base',token['address'],'非发射台放量',NOW,NOW,json.dumps(token)))
    monkeypatch.setattr(g,'recent_alerts',lambda *a,**k:pytest.fail('sound reread a newer database state'))
    packet=sound_ui.snapshot(shown)
    assert packet['latest']['gmgn']==4
    assert [r['id'] for r in packet['events']]==[4,3,2,1]
    assert all(r['source']=='gmgn' for r in packet['events'])
    newer=sound_ui.snapshot(w.dashboard()['gmgn_alerts'])
    assert newer['latest']['gmgn']==99


def test_old_history_refresh_does_not_change_audio_ids_or_times(isolated):
    seed(7)
    first=sound_ui.snapshot(w.dashboard()['gmgn_alerts'])
    with store.connect() as con:con.execute('UPDATE gmgn_alerts SET last_seen=?',(NOW+60,))
    assert sound_ui.snapshot(w.dashboard()['gmgn_alerts'])==first


def test_live_overlay_never_requests_provider_data(isolated,monkeypatch):
    seed()
    monkeypatch.setattr(g.Client,'query',lambda *a,**k:pytest.fail('UI fetched GMGN'))
    monkeypatch.setattr(w.Client,'page',lambda *a,**k:pytest.fail('UI fetched Web3'))
    assert len(w.dashboard()['gmgn_alerts'])==6


def test_alarm_sound_bridge_is_emitted_after_completed_dashboard(isolated,monkeypatch):
    from streamlit.testing.v1 import AppTest
    seed()
    # A marker proves the bridge is called by the board after its complete HTML,
    # using exactly the rows that card renders, not an independent earlier timer.
    script='''
import streamlit as st
from unittest.mock import patch
import monitor_ui, sound_ui, copy_feed
from types import SimpleNamespace
def emit(rows=None):
    st.session_state['played_candidate_ids']=[r['id'] for r in rows]
    st.markdown('AUDIO_BRIDGE_AFTER_FRAME')
empty=dict(rankings={k:dict(rows=[],updated=0) for k in ['ROI','PNL']},histories={},blocked_until=0,error='')
with patch.object(sound_ui,'sound_events',emit),patch.object(copy_feed,'get_monitor',lambda:SimpleNamespace(snapshot=lambda:empty)):
    monitor_ui.render()
'''
    app=AppTest.from_string(script,default_timeout=30).run()
    assert not app.exception
    values=[str(x.value) for x in app.markdown]
    frame=next(i for i,v in enumerate(values) if 'class="live-line"' in v)
    audio=values.index('AUDIO_BRIDGE_AFTER_FRAME')
    assert frame<audio
    assert app.session_state['played_candidate_ids']==[6,5,4,3,2,1]
    assert all(f'HISTORY_{i}' in values[frame] for i in range(1,7))
    assert values[frame].index('class="signal-dashboard"') < values[frame].index('class="rankings-grid market-rankings"')
