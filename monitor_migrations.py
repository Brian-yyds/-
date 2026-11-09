"""Apply the requested monitoring policy once; preserve later saved settings."""
import json
import time
import store

VERSION='2026-11-09-three-panels-v2'
EXTRA_VERSION='2026-11-09-300k-kol-gt3'


def apply():
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        def get(key,default):
            r=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
        def put(key,value):
            con.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,json.dumps(value,allow_nan=False)))
        if get('gmgn_extra_rule_version','')!=EXTRA_VERSION:
            from gmgn_monitor import EXTRA_RULE
            put('gmgn_extra_30d_rule',EXTRA_RULE|dict(checked_at=time.time()))
            put('gmgn_extra_rule_version',EXTRA_VERSION)
            # Clear only the derived AND-view; keep alarm/daily/episode history.
            put('signal_dashboard',{})
        if get('monitor_policy_version','')==VERSION:return False
        if get('monitor_policy_version','')=='2026-11-09-three-panels-v1':
            # The user's clarification changes only the direct-launch threshold.
            put('gmgn_config',get('gmgn_config',{})|dict(other_volume=1000000.))
            put('signal_dashboard',{})
            put('monitor_policy_version',VERSION)
            return True
        cfg=get('settings',{})|dict(spike_enabled=True,spike_top20_only=True,spike_up_pct=5.,spike_down_pct=5.)
        put('settings',cfg)
        gmgn=get('gmgn_config',{})|dict(graduate_volume=200000.,other_volume=1000000.,
            participant_mode='kol_or_smart',participant_min=1,poll_seconds=60)
        put('gmgn_config',gmgn)
        con.execute('UPDATE spike_state SET active=0,event_time=0')
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gmgn_episodes'").fetchone():
            con.execute('UPDATE gmgn_episodes SET active=0,below_since=NULL')
        put('spike_top20_since',time.time())
        put('monitor_policy_version',VERSION)
    return True
