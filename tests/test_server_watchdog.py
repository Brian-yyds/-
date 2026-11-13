from copy import deepcopy
from deploy import server_watchdog as w

NOW=10000


def sample():
    values=dict(gmgn_config=dict(enabled=True),web3_signal_config=dict(enabled=True),
        gmgn_market=dict(heartbeat=NOW-10,chains=dict(sol=dict(updated=NOW-20,last_attempt=NOW-20))),
        web3_signal_health=dict(heartbeat=NOW-10,chains=dict(sol=dict(updated=NOW-20,last_attempt=NOW-20))),
        signal_dashboard=dict(generated_at=NOW-20))
    values['gmgn_market']['chains']={c:dict(updated=NOW-20,last_attempt=NOW-20) for c in w.EXPECTED['gmgn']}
    values['web3_signal_health']['chains']={c:dict(updated=NOW-20,last_attempt=NOW-20) for c in w.EXPECTED['signals']}
    services={unit:dict(state='active',started=NOW-1000) for unit in w.UNITS}
    return values,{},services,dict(gmgn=True,signals=True,workbench=True),dict(heartbeat=NOW-10),True,NOW


def test_healthy_without_any_alarm_does_not_restart():
    faults,notes=w.evaluate(*sample())
    state,actions,_=w.decide(faults,{},NOW)
    assert not actions and not any(faults.values())


def test_stalled_requires_confirmation_bounded_recovery():
    args=list(sample());args[0]['gmgn_market']['heartbeat']=NOW-181
    faults,_=w.evaluate(*args)
    first,actions,_=w.decide(faults,{},NOW);assert actions==[]
    second,actions,_=w.decide(faults,first,NOW+30);assert actions==[w.UNITS[0]]
    third,actions,_=w.decide(faults,second,NOW+60);assert actions==[]
    _,actions,notes=w.decide(faults,third,NOW+90);assert actions==[] and notes
    maxed=dict(history={w.UNITS[0]:[NOW-600,NOW-400,NOW-301]},suspects=first['suspects'])
    assert w.decide(faults,maxed,NOW+30)[1]==[]


def test_pauses_and_provider_backoff_are_preserved():
    args=list(sample());values=args[0]
    values['gmgn_market']['heartbeat']=NOW-1000;values['gmgn_rate_limit_until']=NOW+3600
    values['web3_signal_config']['enabled']=False;values['web3_signal_health']['heartbeat']=0
    values['signal_dashboard']['generated_at']=0;args[3]['signals']=False
    faults,notes=w.evaluate(*args)
    assert not any(faults.values()) and 'gmgn:provider_backoff' in notes and 'signals:paused' in notes


def test_provider_retry_is_not_frozen_and_does_not_reset_backoff():
    args=list(sample());args[0]['gmgn_market']['chains']['sol']=dict(updated=NOW-10000,last_attempt=NOW-10,error='limited',retry_at=NOW+60)
    faults,notes=w.evaluate(*args)
    assert not any(faults.values()) and 'gmgn:provider_retrying:sol' in notes


def test_missing_service_immediate_and_telegram_stall_confirmed():
    args=list(sample());args[2][w.UNITS[1]]['state']='inactive'
    faults,_=w.evaluate(*args);assert w.decide(faults,{},NOW)[1]==[w.UNITS[1]]
    args=list(sample());args[4]['heartbeat']=NOW-91
    faults,_=w.evaluate(*args);state,actions,_=w.decide(faults,{},NOW)
    assert not actions and w.decide(faults,state,NOW+30)[1]==[w.UNITS[1]]
    args=list(sample());args[4].update(failed=1,uncertain=1,pending_due=3)
    faults,notes=w.evaluate(*args)
    assert not any(faults.values()) and 'delivery_records_need_review' in notes


def test_retained_error_cannot_mask_dead_retry_loop():
    args=list(sample());args[0]['web3_signal_health']['chains']['sol']=dict(updated=NOW-1000,last_attempt=NOW-800,next_poll_at=NOW-500,error='timeout')
    faults,_=w.evaluate(*args)
    assert 'signals:scan_stalled:sol' in faults[w.UNITS[0]]


def test_missing_chain_with_healthy_worker_heartbeat_is_detected():
    args=list(sample());del args[0]['gmgn_market']['chains']['base']
    faults,_=w.evaluate(*args)
    assert 'gmgn:scan_stalled:base' in faults[w.UNITS[0]]


def test_incomplete_source_does_not_hide_stale_special_snapshot():
    args=list(sample());args[0]['web3_signal_health']['chains']['sol']['coverage_status']='pagination_no_progress'
    args[0]['web3_signal_health']['chains']['sol']['next_poll_at']=NOW+30
    args[0]['signal_dashboard']['generated_at']=NOW-500
    faults,notes=w.evaluate(*args)
    assert 'special_snapshot_stalled' in faults[w.UNITS[0]]
    assert 'signals:source_coverage_limited:sol' in notes


def test_stale_durable_inbox_is_detected_without_deleting_jobs():
    args=list(sample());args[0]['_inbox']=dict(pending=2,oldest=NOW-400)
    faults,_=w.evaluate(*args)
    assert 'accepted_observations_stalled' in faults[w.UNITS[0]]


def test_release_tamper_is_reported_not_repaired(tmp_path,monkeypatch):
    import hashlib,json
    root=tmp_path/'current';root.mkdir();p=root/'engine.py';p.write_text('original\n')
    (root/'server-standard-manifest.json').write_text(json.dumps(dict(version='v1',files={'engine.py':hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()})))
    monkeypatch.setattr(w,'BASE',tmp_path)
    assert w.release_check()['ok']
    p.write_text('changed\n')
    assert w.release_check()['mismatched']==['engine.py']
    assert p.read_text()=='changed\n'
