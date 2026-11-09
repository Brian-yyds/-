"""Additional rule boundaries, daily latch, expiry and AND-source integration."""
from datetime import datetime
import pytest
import store
import gmgn_monitor as g
import gmgn_tracking as tracking

NOW=datetime(2026,11,9,12,tzinfo=g.ZONE).timestamp()
ADDRESS='C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump'
CFG=g.DEFAULTS|{'extra_30d_rule':g.EXTRA_RULE}


def token(**updates):
    return g.normalize(dict(address=ADDRESS,symbol='TEST',name='Test',volume=300001,
        launchpad='pump',launchpad_platform='Pump.fun',launchpad_status='1',
        open_timestamp=NOW-2*86400,renowned_count=4,smart_degen_count=0)|updates,'sol',NOW)


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'extra.sqlite')


@pytest.mark.parametrize('updates,passes',[
    ({},True),({'volume':300000},False),({'renowned_count':3},False),
    ({'renowned_count':None,'smart_degen_count':100},False),({'volume':None},False),
    ({'volume':float('nan')},False),({'renowned_count':True},False),
    ({'open_timestamp':None},False),({'open_timestamp':NOW-30*86400},True),
    ({'open_timestamp':NOW-30*86400-1},False),({'launchpad_status':'0'},False),
    ({'symbol':'AAPLx','name':'Apple xStock'},False),
    ({'open_timestamp':NOW+1},False),
])
def test_additional_boundaries_use_graduation_and_kol_only(updates,passes):
    assert bool(g.extra_reason(token(**updates),CFG,NOW)) is passes


def test_today_direct_original_thresholds_and_people_unchanged():
    assert g.reason(token(open_timestamp=NOW-60,renowned_count=0,smart_degen_count=1,volume=200001),CFG,NOW)=='今日毕业放量'
    assert not g.reason(token(open_timestamp=NOW-60,volume=200000),CFG,NOW)
    assert not g.reason(token(launchpad='',launchpad_platform='pool_ray',volume=999999),CFG,NOW)
    assert g.reason(token(launchpad='',launchpad_platform='pool_ray',volume=1000001,renowned_count=0,smart_degen_count=1),CFG,NOW)=='非发射台放量'
    assert g.reason(token(),CFG|{'participant_min':100},NOW)=='近30日毕业放量'
    assert not g.reason(token(),CFG|{'extra_30d_rule':g.EXTRA_RULE|{'enabled':False}},NOW)
    midnight=datetime.fromtimestamp(NOW,g.ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    assert not g.extra_reason(token(open_timestamp=midnight),CFG,NOW)
    assert g.extra_reason(token(open_timestamp=midnight-1),CFG,NOW)


def count():
    with store.connect() as con:return con.execute('SELECT count(*) FROM gmgn_alerts').fetchone()[0]


def scan(value,stamp):g.record_scan('sol',[value],CFG,stamp)


def cool(value,stamp):
    for offset in range(0,301,60):scan(value|{'volume':300000},stamp+offset)


def test_restart_daily_latch_and_300k_rearm_no_window_extension(isolated):
    value=token();scan(value,NOW);scan(value,NOW+60)
    assert count()==1
    cool(value,NOW+120);scan(value,NOW+480)
    assert count()==1  # Shared Beijing daily cap.
    scan(value,NOW+86400)
    assert count()==1  # Midnight alone never rearms.
    cool(value,NOW+86460);scan(value,NOW+86820)
    assert count()==2
    row=tracking.watch_rows(NOW+86820)[0]
    assert row['first_alert']==NOW and row['expires_at']==NOW+10*86400


def test_kol_fluctuations_do_not_rearm_and_old_extra_can_fire_after_watch_expiry(isolated):
    value=token();scan(value,NOW)
    for offset in range(60,421,60):scan(value|{'renowned_count':0},NOW+offset)
    scan(value,NOW+86400);assert count()==1
    cool(value,NOW+11*86400);scan(value,NOW+11*86400+360)
    assert count()==2
    assert not tracking.watch_rows(NOW+11*86400+360)  # Original first-alert expiry stays fixed.


def test_migration_enables_only_new_rule_without_resetting_settings_or_history(isolated):
    import monitor_migrations as migration
    store.put('monitor_policy_version',migration.VERSION)
    store.put('gmgn_config',dict(graduate_volume=200000,other_volume=1000000,enabled=False))
    store.put('sync_config',dict(enabled=False,interval_seconds=60))
    store.put('gmgn_extra_30d_rule',dict(enabled=False,day_change_pct=300))
    scan(token(),NOW)
    assert not migration.apply()
    assert g.configuration()['extra_30d_rule']['kol_gt']==3
    assert 'day_change_pct' not in store.get('gmgn_extra_30d_rule')
    assert g.configuration()['enabled'] is False and store.get('sync_config')['enabled'] is False
    scan(token(),NOW+60);assert count()==1


def test_special_alert_uses_same_new_rule_and_fresh_ca_intersection(isolated):
    import web3_signals as w
    store.put('gmgn_extra_30d_rule',g.EXTRA_RULE)
    value=token();g.record_scan('sol',[value],g.configuration(),NOW)
    signal=dict(chain='sol',signal_id='1',address=ADDRESS,triggered=NOW-10,observed=NOW,status='active',direction='buy')
    import json
    with store.connect() as con:
        w.schema(con)
        con.execute('INSERT INTO wallet_signals VALUES (?,?,?,?,?,?,?)',('sol','1',ADDRESS,NOW-10,NOW,'active',json.dumps(signal)))
    store.put('web3_signal_health',dict(chains={'sol':dict(updated=NOW,error='')}))
    result=w.publish(NOW)
    assert len(result['special_alerts'])==1
    assert result['special_alerts'][0]['token']['renowned_count']==4


def test_extra_floor_is_included_when_original_thresholds_are_higher():
    class Client:
        def __init__(self):self.calls=0
        def query(self,args):
            self.calls+=1
            return {'rank':[dict(address='0x'+f'{i:040x}',volume=400000 if self.calls==1 else 0)
                            for i in range(50)] if self.calls==1 else []}
    client=Client()
    _,coverage=g.Monitor(client).candidates('bsc',CFG|dict(graduate_volume=900000,other_volume=1000000))
    assert client.calls==2 and coverage['pages']==2
