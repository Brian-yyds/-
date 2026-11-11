"""Durable dataset intersection: order, reversal, restart, truncation and atomicity."""
from datetime import datetime
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_web3_signals import isolated,NOW,ADDRESS,token,raw,save_signal,special_count
import store,gmgn_monitor as g,web3_signals as w
import signal_ingest


def accept(row=None,stamp=NOW):
    row=row or w.normalize(raw(),'sol',stamp)
    w.record_wallet_batch(row['chain'],[row],stamp,dict(updated=stamp,error='',count=1))


def test_gmgn_first_then_volume_falls_then_web3_still_matches_immediately(isolated):
    g.record_scan('sol',[token()],g.configuration(),NOW)
    g.record_scan('sol',[token(volume=100)],g.configuration(),NOW+20)
    accept(stamp=NOW+60)
    assert special_count()==1
    event=w.dashboard()['special_alerts'][0]
    assert event['token']['volume']==200001 and event['gmgn_alert_id']
    assert event['watch_expires_at']==NOW+10*86400
    # Still recorded even when both latest live states subsequently stop qualifying.
    accept(w.normalize(raw(status='completed'),'sol',NOW+70),NOW+70)
    g.record_scan('sol',[token(volume=0)],g.configuration(),NOW+80)
    assert special_count()==1 and w.dashboard()['special_alerts'][0]==event


def test_web3_first_then_expires_then_gmgn_matches_immediately(isolated):
    accept()
    accept(w.normalize(raw(status='completed'),'sol',NOW+30),NOW+30)
    assert special_count()==0
    g.record_scan('sol',[token()],g.configuration(),NOW+60)
    assert special_count()==1
    assert w.dashboard()['special_alerts'][0]['signal']['status']=='active'


def test_original_ten_day_anchor_never_extended_by_repeat_alert(isolated):
    g.record_scan('sol',[token()],g.configuration(),NOW)
    # Simulate a later accepted daily alert without moving first-alert expiry.
    with store.connect() as con:
        con.execute('UPDATE gmgn_watch SET last_alert=?',(NOW+9*86400,))
    stamp=NOW+10*86400
    accept(w.normalize(raw(signalId=2,signalTriggerTime=stamp*1000),'sol',stamp),stamp)
    assert special_count()==0


def test_wallet_evidence_beyond_ten_days_not_eligible(isolated):
    accept()
    stamp=NOW+10*86400+1
    g.record_scan('sol',[token(open_timestamp=stamp-60)],g.configuration(),stamp)
    assert special_count()==0


def test_daily_dedup_same_web3_event_across_midnight_and_restart(isolated):
    accept();g.record_scan('sol',[token()],g.configuration(),NOW)
    for offset in (1,50,86400):w.publish(NOW+offset)
    assert special_count()==1
    stamp=NOW+86400
    accept(w.normalize(raw(signalId=2,signalTriggerTime=stamp*1000),'sol',stamp),stamp)
    assert special_count()==2
    accept(w.normalize(raw(signalId=3,signalTriggerTime=(stamp+10)*1000),'sol',stamp+10),stamp+10)
    assert special_count()==2
    w.publish(NOW+2*86400)
    assert special_count()==2  # Yesterday's suppressed signals never drip out today.


def test_new_late_arriving_event_not_hidden_by_a_newer_already_used_signal(isolated):
    accept();g.record_scan('sol',[token()],g.configuration(),NOW)
    stamp=NOW+86400
    late=w.normalize(raw(signalId=2,signalTriggerTime=(NOW-100)*1000),'sol',stamp)
    accept(late,stamp)
    assert special_count()==2
    assert w.dashboard()['special_alerts'][0]['signal']['signal_id']=='2'


def test_match_is_independent_of_display_limits_and_latest_status(isolated):
    accept();accept(w.normalize(raw(status='completed'),'sol',NOW+1),NOW+1)
    with store.connect() as con:
        for i in range(2,302):
            row=w.normalize(raw(address='1'*32,signalId=i,status='completed',signalTriggerTime=(NOW+1)*1000),'sol',NOW+1)
            con.execute('INSERT INTO wallet_signals VALUES (?,?,?,?,?,?,?)',
                ('sol',row['signal_id'],row['address'],row['triggered'],row['observed'],row['status'],json.dumps(row)))
    g.record_scan('sol',[token()],g.configuration(),NOW+2)
    assert special_count()==1


def test_no_false_join_for_same_symbol_wrong_chain_or_ca(isolated):
    accept(w.normalize(raw(address='1'*32),'sol',NOW))
    g.record_scan('sol',[token()],g.configuration(),NOW)
    g.record_scan('bsc',[token('bsc','0x'+'a'*40)],g.configuration(),NOW)
    assert special_count()==0


def test_gmgn_alarm_and_evidence_rollback_together_on_failure(isolated,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('injected transaction interruption')
    monkeypatch.setattr(w,'reconcile',fail)
    assert not g.record_scan('sol',[token()],g.configuration(),NOW)
    with store.connect() as con:
        g.schema(con);w.schema(con)
        assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM gmgn_alarm_evidence').fetchone()[0]==0
    assert len(list((store.USER/'signal_inbox').glob('*-gmgn-*.json')))==1


def test_wallet_event_and_special_rollback_together_on_failure(isolated,monkeypatch):
    g.record_scan('sol',[token()],g.configuration(),NOW)
    real=w.reconcile
    monkeypatch.setattr(w,'reconcile',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('interrupted')))
    accept()
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM wallet_signal_events').fetchone()[0]==0
    monkeypatch.setattr(w,'reconcile',real)
    assert signal_ingest.drain_all()['pending']==0
    assert special_count()==1


def test_source_retry_after_accepted_event_does_not_erase_fact(isolated):
    accept();store.put('web3_signal_health',dict(chains=dict(sol=dict(updated=NOW,error='retry'))))
    g.record_scan('sol',[token()],g.configuration(),NOW+5)
    assert special_count()==1


def test_pagination_continues_and_records_before_later_page_failure(isolated):
    class Pages:
        def __init__(self):self.calls=[]
        def page(self,chain,page=1):
            self.calls.append((chain,page))
            if chain=='bsc':return []
            if page==2:raise w.SignalError('second page temporarily unavailable')
            return [w.normalize(raw(signalId=i),'sol',NOW) for i in range(1,101)]
    client=Pages();w.Monitor(client).step(NOW)
    assert ('sol',2) in client.calls
    with store.connect() as con:assert con.execute('SELECT COUNT(*) FROM wallet_signal_events').fetchone()[0]==100
    g.record_scan('sol',[token()],g.configuration(),NOW+1)
    assert special_count()==1


def test_long_history_uses_continuation_cursor_without_silent_truncation(isolated,monkeypatch):
    class Pages:
        def __init__(self):self.calls=[]
        def page(self,chain,page=1):
            self.calls.append((chain,page))
            if chain=='bsc' or page>=8:return []
            return [w.normalize(raw(signalId=page*100+i),'sol',w.time.time()) for i in range(100)]
    client=Pages();w.Monitor(client).step(NOW)
    health=store.get('web3_signal_health')['chains']['sol']
    assert health['capped'] and health['scan_next_page']==6
    monkeypatch.setattr(w.time,'time',lambda:NOW+60)
    w.Monitor(client).step(NOW+60)
    assert ('sol',6) in client.calls and ('sol',7) in client.calls and ('sol',8) in client.calls
    assert store.get('web3_signal_health')['chains']['sol']['coverage_status']=='complete'
    with store.connect() as con:assert con.execute('SELECT COUNT(*) FROM wallet_signal_events').fetchone()[0]==700


def test_concurrent_source_commits_produce_exactly_one_match(isolated):
    item=token();cfg=g.configuration()
    with store.connect() as con:g.schema(con);w.schema(con)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[]
        for _ in range(4):
            futures.append(pool.submit(accept))
            futures.append(pool.submit(g.record_scan,'sol',[item],cfg,NOW))
        for future in futures:future.result()
    assert signal_ingest.drain_all()['pending']==0
    assert special_count()==1
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM gmgn_alarm_evidence').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM wallet_signal_events').fetchone()[0]==1


def test_legacy_accepted_rows_migrate_without_latest_volume_recheck(isolated):
    g.record_scan('sol',[token()],g.configuration(),NOW)
    g.record_scan('sol',[token(volume=0)],g.configuration(),NOW+1)
    # A pre-upgrade DB has no evidence tables; raw alarms and accepted valid rows remain.
    save_signal()
    with store.connect() as con:
        con.execute('DELETE FROM gmgn_alarm_evidence');con.execute('DELETE FROM wallet_signal_events')
    assert len(w.publish(NOW+10)['special_alerts'])==1


def test_queued_gmgn_hit_is_replayed_before_newer_low_volume(isolated,monkeypatch):
    import gmgn_tracking
    actual=gmgn_tracking.record_scan
    def busy(*args,**kwargs):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(gmgn_tracking,'record_scan',busy)
    assert not g.record_scan('sol',[token()],g.configuration(),NOW)
    monkeypatch.setattr(gmgn_tracking,'record_scan',actual)
    assert g.record_scan('sol',[token(volume=0)],g.configuration(),NOW+10)
    accept(stamp=NOW+20)
    assert special_count()==1
    assert w.dashboard()['special_alerts'][0]['token']['volume']==200001
    assert signal_ingest.drain_all()['pending']==0


def test_crash_after_commit_before_inbox_ack_cannot_duplicate_alarm(isolated,monkeypatch):
    actual=signal_ingest.apply
    def interrupted(job):actual(job);raise RuntimeError('crash after commit')
    monkeypatch.setattr(signal_ingest,'apply',interrupted)
    assert not g.record_scan('sol',[token()],g.configuration(),NOW)
    monkeypatch.setattr(signal_ingest,'apply',actual)
    assert signal_ingest.drain_all()['pending']==0
    accept()
    assert special_count()==1
    with store.connect() as con:assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==1


def test_current_pause_is_respected_even_when_source_config_was_captured_before_pause(isolated):
    captured=g.configuration()
    store.put('gmgn_config',dict(enabled=False))
    accept()
    g.record_scan('sol',[token()],captured,NOW)
    assert special_count()==0
    store.put('gmgn_config',dict(enabled=True))
    assert len(w.publish(NOW+1)['special_alerts'])==1


def test_six_day_old_alarm_outside_visible_hundred_still_matches_with_explicit_origin(isolated):
    import signal_ui
    g.record_scan('sol',[token()],g.configuration(),NOW)
    with store.connect() as con:
        for i in range(120):
            other=token('base','0x'+f'{i+1:040x}',symbol=f'NEWER_{i}',launchpad='',launchpad_platform='',volume=1000001)
            con.execute('INSERT INTO gmgn_alerts(chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?)',
                        ('base',other['address'],'非发射台放量',NOW+1+i,NOW+1+i,json.dumps(other)))
    stamp=NOW+6*86400
    accept(w.normalize(raw(signalId=700,signalTriggerTime=stamp*1000),'sol',stamp),stamp)
    view=w.dashboard()
    assert len(view['gmgn_alerts'])==100 and all(row['token']['address']!=ADDRESS for row in view['gmgn_alerts'])
    assert len(view['special_alerts'])==1
    event=view['special_alerts'][0]
    assert event['gmgn_alert_id']==1 and event['gmgn_triggered']==NOW and event['triggered']==stamp
    markup=signal_ui.special_card(view)
    assert '历史GMGN匹配' in markup and 'GMGN原报 '+signal_ui.clock(NOW) in markup
    assert '记录市值' in markup
    with store.connect() as con:assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==121


def test_repeat_qualifying_scan_preserves_alarm_snapshot_and_updates_only_live_caches(isolated):
    original=token(volume=200001,market_cap=123456)
    g.record_scan('sol',[original],g.configuration(),NOW)
    g.record_scan('sol',[token(volume=900000,market_cap=777777)],g.configuration(),NOW+300)
    with store.connect() as con:
        row=con.execute('SELECT data,last_seen FROM gmgn_alerts').fetchone()
        evidence=json.loads(con.execute('SELECT data FROM gmgn_alarm_evidence').fetchone()[0])
        current=json.loads(con.execute('SELECT token FROM gmgn_watch').fetchone()[0])
        assert json.loads(row[0])['volume']==200001 and row[1]==NOW+300
        assert evidence['volume']==200001 and current['volume']==900000
        assert con.execute('SELECT market_cap FROM gmgn_market_caps').fetchone()[0]==777777
