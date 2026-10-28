from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import pytest
import gmgn_monitor as g
import gmgn_tracking as t
import store
import data_export

NOW=datetime(2026,10,29,12,tzinfo=g.ZONE).timestamp()
ADDRESS='C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'tracking.sqlite')
    monkeypatch.setattr(g.time,'time',lambda:NOW)


def token(**updates):
    return g.normalize(dict(address=ADDRESS,symbol='TEST',name='Test',volume=200001,
        launchpad='pump',launchpad_platform='Pump.fun',launchpad_status='1',open_timestamp=NOW-60,
        renowned_count=3,smart_degen_count=1,market_cap=123456,holder_count=100)|updates,'sol',NOW)


def cool(stamp,item=None):
    for offset in range(0,316,15):
        g.record_scan('sol',[(item or token())|dict(volume=0)],g.DEFAULTS,stamp+offset)


def count():
    with store.connect() as con:return con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]


def test_one_per_beijing_day_after_rearm_ack_and_restart(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    g.acknowledge(g.pending_ids());cool(NOW+15)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+345)
    assert count()==1
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+86400)
    assert count()==1  # Still above threshold at midnight is not a new breakout.
    cool(NOW+86415)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+86745)
    assert count()==2
    assert g.recent_alerts()[0]['rule']=='毕业代币再次放量'
    rows=t.watch_rows(NOW+86745)
    assert rows[0]['first_alert']==NOW and rows[0]['expires_at']==NOW+10*86400


def test_daily_boundary_is_beijing_and_shared_across_rule_branches(isolated):
    stamp=datetime(2026,10,29,23,50,tzinfo=g.ZONE).timestamp()
    first=token(open_timestamp=stamp-60)
    g.record_scan('sol',[first],g.DEFAULTS,stamp)
    cool(stamp+15,first)
    direct=first|dict(launchpad='',launchpad_platform='pool_ray',volume=1000001)
    g.record_scan('sol',[direct],g.DEFAULTS,stamp+345)
    assert count()==1
    cool(stamp+360,first)
    g.record_scan('sol',[direct],g.DEFAULTS,stamp+690)
    assert count()==2 and t.day(stamp)!=t.day(stamp+690)


def test_expiry_never_extends_or_restarts_the_same_address_window(isolated):
    direct=token(launchpad='',launchpad_platform='',volume=1000001,open_timestamp=0)
    g.record_scan('sol',[direct],g.DEFAULTS,NOW)
    cool(NOW+9*86400,direct)
    g.record_scan('sol',[direct],g.DEFAULTS,NOW+9*86400+330)
    assert count()==2
    assert t.watch_rows(NOW+10*86400-1)
    assert not t.watch_rows(NOW+10*86400)
    cool(NOW+10*86400,direct)
    g.record_scan('sol',[direct],g.DEFAULTS,NOW+11*86400)
    assert count()==2


def test_missing_observations_do_not_rearm_and_old_graduate_needs_previous_alert(isolated):
    assert not g.reason(token(open_timestamp=NOW-86400),g.DEFAULTS,NOW)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    for offset in range(15,331,15):g.record_scan('sol',[token(volume=None)],g.DEFAULTS,NOW+offset)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+86400)
    assert count()==1


def test_concurrent_duplicate_observations_create_one_alert(isolated):
    item=token()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:g.record_scan('sol',[item],g.DEFAULTS,NOW),range(4)))
    assert count()==1


def test_kol_noise_does_not_rearm_a_continuing_volume_episode(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    for offset in range(15,346,15):g.record_scan('sol',[token(renowned_count=0)],g.DEFAULTS,NOW+86400+offset)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+86760)
    assert count()==1


def test_legacy_migration_preserves_first_time_excludes_equity_and_deduplicates_days(isolated):
    with store.connect() as con:
        g.schema(con)
        for stamp,name,address in [(NOW-86400,'FIRST',ADDRESS),(NOW-86300,'SECOND',ADDRESS),
                                    (NOW,'SpaceX - Backpack Securities','SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb')]:
            item=token(symbol=name,address=address,name=name)
            con.execute('INSERT INTO gmgn_alerts(chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?)',
                        ('sol',address,'今日毕业放量',stamp,stamp,json.dumps(item)))
    rows=t.watch_rows(NOW)
    assert len(rows)==1 and rows[0]['first_alert']==NOW-86400
    assert len(t.watch_rows(NOW))==1
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM gmgn_daily').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==3


def test_hour_ranking_only_real_alerts_top10_fresh_and_expiring(isolated):
    for i in range(13):
        item=token(address='A'*31+'123456789ABCD'[i],launchpad='',launchpad_platform='',volume=1000001)
        first=NOW-11*86400 if i==12 else NOW-1000+i
        g.record_scan('sol',[item],g.DEFAULTS,first)
        with store.connect() as con:
            con.execute('UPDATE gmgn_watch SET volume_1h=?,volume_observed=? WHERE address=?',
                        (i*100000,NOW-181 if i==11 else NOW,item['address']))
    snap=t.ranking_snapshot(NOW)
    assert snap['total']==12 and snap['fresh']==11 and not snap['complete']
    assert len(snap['rows'])==10
    assert [r['volume_1h'] for r in snap['rows']]==[i*100000 for i in range(10,0,-1)]
    assert t.ranking_snapshot(NOW+181)['rows']==[]


def test_info_uses_nested_real_1h_volume_not_5m_or_toplist(isolated):
    raw=dict(address=ADDRESS,symbol='LIVE',price=dict(price='2',volume_5m='200001',volume_1h='999999'),
             circulating_supply='10000',wallet_tags_stat=dict(smart_wallets=1,renowned_wallets=3),
             launchpad='pump',launchpad_platform='Pump.fun',launchpad_status='1',open_timestamp=NOW-60)
    item,volume=t.parse_info(raw,'sol',ADDRESS,NOW)
    assert volume==999999 and item['volume']==200001 and item['market_cap']==20000
    assert g.reason(item,g.DEFAULTS,NOW)
    with pytest.raises(ValueError):t.parse_info(raw,'sol','1'*32,NOW)
    assert t.parse_info(raw|dict(price=dict(volume_5m=123,volume_1h=None)),'sol',ADDRESS,NOW)[1] is None
    assert t.parse_info(raw|dict(price=dict(volume_5m=0,volume_1h=0)),'sol',ADDRESS,NOW)[1]==0


class FakeClient:
    def __init__(self):self.calls=[]
    def query(self,args):
        self.calls.append(args)
        if args[:2]==['token','info-batch']:
            return [dict(chain=p['chain'],address=p['address'],ok=True,observed=NOW,
                         data=dict(address=p['address'],name='Live',symbol='LIVE',price=dict(price=1,volume_5m=0,volume_1h=876543),
                                   circulating_supply=10000,wallet_tags_stat=dict(smart_wallets=1,renowned_wallets=3)))
                    for p in json.loads(args[args.index('--params')+1])]
        assert args[:2]==['market','trending']
        return dict(rank=[])


def test_detail_watch_survives_leaving_candidate_list_and_old_leaders_removed(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW-60)
    store.put('gmgn_market',dict(hot=token(),volume_leader=token(),hot_updated=NOW))
    client=FakeClient();g.Monitor(client).cycle()
    snap=store.get('gmgn_market')
    assert not any(k in snap for k in ('hot','volume_leader','hot_updated'))
    assert len([c for c in client.calls if c[0]=='market'])==4
    assert len([c for c in client.calls if c[0]=='token'])==1
    assert t.ranking_snapshot(NOW)['rows'][0]['volume_1h']==876543
    from monitor_ui import gmgn_combined_card
    markup=gmgn_combined_card(NOW)
    assert '10日报警榜' in markup and '1h成交额' in markup
    assert '热搜' not in markup and '50天' not in markup and '5m 成交额 #1' not in markup
    public=data_export.gmgn_data(dict(now=NOW))
    assert public['watch_ranking']['rows'][0]['volume_1h']==876543
    assert 'hot' not in public and 'volume_leader' not in public
    assert public['rules']['daily_limit']==1 and public['rules']['tracking_days']==10


def test_one_chain_failure_does_not_stop_other_chains_or_watched_info(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW-60)
    class Partial(FakeClient):
        def query(self,args):
            if args[0]=='market' and args[args.index('--chain')+1]=='sol':raise g.QueryError('test')
            return super().query(args)
    client=Partial()
    with pytest.raises(g.QueryError):g.Monitor(client).cycle()
    assert len([c for c in client.calls if c[0]=='market'])==3
    assert t.ranking_snapshot(NOW)['fresh']==1
