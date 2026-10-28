from datetime import datetime
import json
import pytest
import gmgn_monitor as g
import store

NOW = datetime(2026,10,25,12,tzinfo=g.ZONE).timestamp()
ADDRESS = 'C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump'


def token(**extra):
    raw = dict(address=ADDRESS,symbol='TEST',name='Test token',launchpad='pump',launchpad_platform='Pump.fun',
               launchpad_status='1',open_timestamp=NOW-3600,creation_timestamp=NOW-86400,
               volume=200001,smart_degen_count=1,renowned_count=3,market_cap=1000000,holder_count=350)
    raw.update(extra)
    return g.normalize(raw,'sol',NOW)


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'gmgn.sqlite')


def test_counts_sum_and_strict_threshold():
    assert g.reason(token(),g.DEFAULTS,NOW)=='今日毕业放量'
    assert not g.reason(token(smart_degen_count=0,renowned_count=3),g.DEFAULTS,NOW)
    assert not g.reason(token(volume=200000),g.DEFAULTS,NOW)
    assert not g.reason(token(volume=199999),g.DEFAULTS,NOW)
    assert g.reason(token(smart_degen_count=0,renowned_count=4),g.DEFAULTS,NOW)
    assert not g.reason(token(smart_degen_count=4,renowned_count=2),g.DEFAULTS,NOW)


def test_graduation_uses_beijing_calendar_not_creation_or_rolling_24h():
    midnight=datetime(2026,10,25,tzinfo=g.ZONE).timestamp()
    assert g.reason(token(open_timestamp=midnight),g.DEFAULTS,NOW)
    assert not g.reason(token(open_timestamp=midnight-1,creation_timestamp=NOW-10),g.DEFAULTS,NOW)
    assert not g.reason(token(open_timestamp=0,creation_timestamp=NOW-10),g.DEFAULTS,NOW)
    assert not g.reason(token(open_timestamp=NOW+1),g.DEFAULTS,NOW)


@pytest.mark.parametrize('status',['0','',None])
def test_ungraduated_never_uses_million_bypass(status):
    assert not g.reason(token(launchpad_status=status,volume=2000000),g.DEFAULTS,NOW)


def test_direct_pool_million_rule_requires_kol_but_not_smart_or_date():
    direct=token(launchpad='',launchpad_platform='pool_pancake',volume=1000001,open_timestamp=0,
                 smart_degen_count=0,renowned_count=3)
    assert g.reason(direct,g.DEFAULTS,NOW)=='非发射台放量'
    assert not g.reason(direct|{'volume':1000000},g.DEFAULTS,NOW)
    assert not g.reason(direct|{'volume':999999},g.DEFAULTS,NOW)
    assert not g.reason(direct|{'renowned_count':2},g.DEFAULTS,NOW)
    assert not g.reason(direct|{'renowned_count':None},g.DEFAULTS,NOW)
    # Migrated launchpad token can retain a DEX label. Do not treat it as direct.
    assert not g.reason(direct|{'launchpad':'fourmeme','open_timestamp':NOW-86400},g.DEFAULTS,NOW)


@pytest.mark.parametrize('field',['volume','smart_degen_count','renowned_count','open_timestamp'])
def test_missing_numeric_fields_do_not_fabricate_alerts(field):
    assert not g.reason(token(**{field:None}),g.DEFAULTS,NOW)
    assert not g.reason(token(**{field:float('nan')}),g.DEFAULTS,NOW)


def test_missing_launchpad_data_is_unknown_not_direct():
    raw=dict(address=ADDRESS,volume=2000000)
    assert not g.reason(g.normalize(raw,'sol',NOW),g.DEFAULTS,NOW)


def test_duplicate_latched_across_restart_and_ack(isolated):
    t=token()
    g.record_scan('sol',[t],g.DEFAULTS,NOW)
    first=g.pending_alerts()
    g.record_scan('sol',[t],g.DEFAULTS,NOW+15)
    assert len(g.pending_alerts())==1
    g.acknowledge(g.pending_ids())
    g.record_scan('sol',[t],g.DEFAULTS,NOW+30)
    assert not g.pending_alerts()
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM gmgn_alerts').fetchone()[0]==1
    assert first[0]['token']['address']==ADDRESS


def test_missing_top_n_and_outage_do_not_rearm(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    g.acknowledge(g.pending_ids())
    g.record_scan('sol',[],g.DEFAULTS,NOW+600)
    g.record_scan('sol',[token(volume=100000)],g.DEFAULTS,NOW+900)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+915)
    assert not g.pending_alerts()


def test_five_observed_minutes_below_rule_rearms(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    g.acknowledge(g.pending_ids())
    for offset in range(15,331,15):
        g.record_scan('sol',[token(volume=100000)],g.DEFAULTS,NOW+offset)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+345)
    assert len(g.pending_alerts())==1


def test_missing_fields_cannot_rearm_an_episode(isolated):
    g.record_scan('sol',[token()],g.DEFAULTS,NOW)
    g.acknowledge(g.pending_ids())
    for offset in range(15,331,15):
        g.record_scan('sol',[token(volume=None)],g.DEFAULTS,NOW+offset)
    g.record_scan('sol',[token()],g.DEFAULTS,NOW+345)
    assert not g.pending_alerts()


def test_runtime_threshold_changes(isolated):
    store.put('gmgn_config',dict(graduate_volume=300000,people=5))
    assert not g.reason(token(),g.configuration(),NOW)
    assert g.configuration()['other_volume']==1000000


class FakeClient:
    def __init__(self): self.calls=[]
    def query(self,args):
        self.calls.append(args)
        if args[1]=='hot-searches':
            return [dict(chain=c,interval='5m',tokens=[dict(symbol='HOT_'+c,name='Hot '+c,address=ADDRESS if c=='sol' else '0x'+'a'*40,
                         volume=12345,visiting_count=i,launchpad='',launchpad_platform='',creation_timestamp=NOW-86400,market_cap=1000000)])
                    for i,c in enumerate(g.CHAINS,10)]
        chain=args[args.index('--chain')+1]
        return {'rank':[dict(address=ADDRESS if chain=='sol' else '0x'+'b'*40,symbol='VOL_'+chain,name='Volume '+chain,
                             volume=700000-g.CHAINS.index(chain)*100000,launchpad='',launchpad_platform='',creation_timestamp=NOW-86400,market_cap=1000000)]}


def test_four_chain_scan_search_and_volume_have_independent_leaders(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    client=FakeClient()
    g.Monitor(client).cycle()
    snap=store.get('gmgn_market')
    assert set(snap['chains'])==set(g.CHAINS)
    assert snap['hot']['chain']=='robinhood'
    assert snap['hot']['volume']==12345
    assert snap['volume_leader']['chain']=='sol'
    assert snap['volume_leader']['volume']==700000
    assert all('5m' in c for c in client.calls[1:])
    assert len([c for c in client.calls if '--max-created' in c])==4
    assert client.calls[0][1]=='hot-searches'
    params=json.loads(client.calls[0][client.calls[0].index('--params')+1])
    assert [p['chain'] for p in params]==list(g.CHAINS)
    assert all(p['interval']=='5m' and p['limit']==500 and p['filters']==[] for p in params)
    assert all(not any(k.startswith(('min_','max_')) for k in p) for p in params)
    assert not g.pending_alerts()


def test_robinhood_rules_and_links_use_correct_chain(isolated):
    raw=token()|dict(address='0x'+'a'*40)
    rob=g.normalize(raw,'robinhood',NOW)
    assert g.reason(rob,g.DEFAULTS,NOW)=='今日毕业放量'
    assert g.token_url(rob)=='https://gmgn.ai/robinhood/token/0x'+'a'*40
    assert not g.valid_address('rob',rob['address'])


def test_idle_volume_card_and_hot_card_show_different_leaders(isolated,monkeypatch):
    from monitor_ui import gmgn_card
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    g.Monitor(FakeClient()).cycle()
    hot=gmgn_card(NOW,'hot')
    volume=gmgn_card(NOW,'volume')
    assert 'HOT_robinhood' in hot and 'VOL_sol' not in hot
    assert 'VOL_sol' in volume and 'HOT_robinhood' not in volume
    assert '5m 成交额 #1' in volume
    assert 'ROB' in hot and '≤30M' in volume


def test_alerts_do_not_override_leaders_and_history_is_separate(isolated):
    from monitor_ui import gmgn_card,quick_prices,gmgn_alarm_card,gmgn_combined_card
    store.put('gmgn_market',dict(hot=token(symbol='HOT'),volume_leader=token(symbol='VOLUME'),hot_scope=g.HOT_SEARCH_SCOPE,hot_updated=NOW,last_success=NOW,status='监控中'))
    g.record_scan('sol',[token(symbol='ALERT')],g.DEFAULTS,NOW)
    hot=gmgn_card(NOW,'hot')
    volume=gmgn_card(NOW,'volume')
    assert 'HOT' in hot and 'VOLUME' in volume
    assert 'ALERT' not in hot+volume
    assert 'ALERT' in gmgn_alarm_card()
    assert 'data-gmgn-kind=' not in quick_prices(NOW)
    html=gmgn_combined_card(NOW)
    assert html.count('data-gmgn-kind=')==3
    assert html.count('data-gmgn-kind="alarms"')==1


@pytest.mark.parametrize('status',['0',0])
def test_unopened_token_cannot_bypass_rule_with_empty_platform(status):
    assert not g.reason(token(launchpad='',launchpad_platform='',launchpad_status=status,volume=2000000),g.DEFAULTS,NOW)


def test_disabled_monitor_makes_no_provider_calls(isolated):
    store.put('gmgn_config',{'enabled':False})
    client=FakeClient()
    g.Monitor(client).cycle()
    assert client.calls==[]


def test_validated_links_and_read_only_client():
    assert g.token_url(token()).startswith('https://gmgn.ai/sol/token/')
    assert not g.token_url(token()|{'address':'javascript:alert(1)'})
    with pytest.raises(ValueError):
        g.Client().query(['swap','submit'])


def test_widget_escapes_metadata_in_separate_history(isolated):
    from monitor_ui import gmgn_card,gmgn_alarm_card
    store.put('gmgn_market',dict(hot=token(symbol='HOT'),hot_scope=g.HOT_SEARCH_SCOPE,hot_updated=NOW,last_success=NOW,status='监控中'))
    assert '热搜 #1' in gmgn_card(NOW)
    g.record_scan('sol',[token(symbol='<script>alert(1)</script>')],g.DEFAULTS,NOW)
    markup=gmgn_alarm_card()
    assert 'GMGN 警报' in markup and '今日毕业放量' in markup
    assert '<script>' not in markup and '&lt;script&gt;' in markup
    assert 'HOT' not in markup
    assert all(k in markup for k in ['市值','持有人'])
    assert 'gmgn-mini-name' in markup and 'class="gmgn-ca"' not in markup


def test_leader_age_and_cap_bounds_include_exact_limits():
    assert g.leader_eligible(token(creation_timestamp=NOW-50*86400,market_cap=30000000),NOW)
    assert not g.leader_eligible(token(creation_timestamp=NOW-50*86400-1),NOW)
    assert not g.leader_eligible(token(market_cap=30000001),NOW)
    assert not g.leader_eligible(token(creation_timestamp=NOW+1),NOW)


def test_configurable_kol_and_leader_bounds_reach_upstream(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    store.put('gmgn_config',dict(kol_count=3,leader_days=20,leader_cap=2000000.))
    cfg=g.configuration()
    assert not g.reason(token(),cfg,NOW)
    assert g.reason(token(renowned_count=4),cfg,NOW)
    assert not g.leader_eligible(token(creation_timestamp=NOW-21*86400),NOW,cfg)
    assert not g.leader_eligible(token(market_cap=2000001),NOW,cfg)
    client=FakeClient()
    g.Monitor(client).cycle()
    filtered=[c for c in client.calls if '--max-created' in c]
    assert all(c[c.index('--max-created')+1]=='20d' for c in filtered)
    params=json.loads(client.calls[0][client.calls[0].index('--params')+1])
    assert all('max_created' not in p and 'max_marketcap' not in p for p in params)


@pytest.mark.parametrize('field',['creation_timestamp','market_cap'])
def test_unknown_leader_filter_values_are_not_eligible(field):
    assert not g.leader_eligible(token(**{field:None}),NOW)
    assert not g.leader_eligible(token(**{field:0}),NOW)


def test_history_keeps_only_three_latest_including_acknowledged(isolated):
    from monitor_ui import gmgn_alarm_card
    for i in range(5):
        t=token(symbol='ALARM_'+str(i))|dict(chain='base',address='0x'+f'{i+1:040x}')
        g.record_scan('base',[t],g.DEFAULTS,NOW+i)
    g.acknowledge(g.pending_ids())
    recent=g.recent_alerts()
    assert [r['token']['symbol'] for r in recent]==['ALARM_4','ALARM_3','ALARM_2']
    assert all(r['acknowledged'] for r in recent)
    markup=gmgn_alarm_card()
    assert markup.count('class="gmgn-token gmgn-mini"')==3
    assert 'ALARM_1' not in markup
    assert '今日毕业放量' in markup  # available in hover text
    assert '5m成交' not in markup and 'class="gmgn-ca"' not in markup
    g.record_scan('sol',[],g.DEFAULTS,NOW+40*86400)
    assert len(g.recent_alerts())==3  # retain the latest three even after archive pruning


def test_leader_filters_do_not_silently_change_alarm_rules():
    t=token(launchpad='',launchpad_platform='',volume=1500001,market_cap=60000000,creation_timestamp=NOW-100*86400)
    assert not g.leader_eligible(t,NOW)
    assert g.reason(t,g.DEFAULTS,NOW)=='非发射台放量'


def test_hot_search_is_unfiltered_while_volume_leader_stays_filtered(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    class MixedClient(FakeClient):
        def query(self,args):
            result=super().query(args)
            if args[1]=='hot-searches':
                for block in result:
                    sample=block['tokens'][0]
                    block['tokens']=[sample|{'symbol':'TOO_OLD','creation_timestamp':NOW-51*86400,'visiting_count':1e9},
                                     sample|{'symbol':'TOO_BIG','market_cap':30000001,'visiting_count':1e8},sample]
            elif '--max-created' in args:
                sample=result['rank'][0]
                result['rank']=[sample|{'symbol':'TOO_OLD','creation_timestamp':NOW-51*86400,'volume':1e9},
                                sample|{'symbol':'TOO_BIG','market_cap':30000001,'volume':1e8},sample]
            return result
    g.Monitor(MixedClient()).cycle()
    snapshot=store.get('gmgn_market')
    assert snapshot['hot']['symbol']=='TOO_OLD'
    assert snapshot['volume_leader']['symbol']=='VOL_sol'
    from monitor_ui import gmgn_card
    markup=gmgn_card(NOW,'hot')
    assert 'TOO_OLD' in markup and '50天' not in markup and '≤30M' not in markup
    assert '5m 成交额 ↓' in markup


def test_missing_age_cap_and_alarm_counts_do_not_hide_hot_search(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    class SparseClient(FakeClient):
        def query(self,args):
            result=super().query(args)
            if args[1]=='hot-searches':
                raw=result[0]['tokens'][0]
                result[0]['tokens']=[raw|dict(symbol='CURRENT',creation_timestamp=None,market_cap=None,visiting_count=999999)]
                result.append(dict(chain='eth',interval='5m',tokens=[raw|dict(symbol='OUTSIDE_SCOPE',address='0x'+'a'*40,volume=1e12,visiting_count=1e10)]))
            return result
    g.Monitor(SparseClient()).cycle()
    from monitor_ui import gmgn_card
    assert 'CURRENT' in gmgn_card(NOW,'hot')
    assert 'OUTSIDE_SCOPE' not in gmgn_card(NOW,'hot')
    assert not g.pending_alerts()


def test_volume_failure_does_not_delay_publishing_current_hot_search(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    class PartialClient(FakeClient):
        def query(self,args):
            if args[1]=='trending': raise g.QueryError('成交榜暂不可用')
            return super().query(args)
    with pytest.raises(g.QueryError): g.Monitor(PartialClient()).cycle()
    snapshot=store.get('gmgn_market')
    assert snapshot['hot']['symbol']=='HOT_robinhood'
    assert snapshot['hot_updated']==NOW


def test_old_filtered_hot_cache_is_not_presented_as_current_unfiltered_search(isolated):
    from monitor_ui import gmgn_card
    store.put('gmgn_market',dict(hot=token(symbol='OLD_FILTERED'),last_success=NOW,status='监控中'))
    assert 'OLD_FILTERED' not in gmgn_card(NOW,'hot')
    assert '正在更新四链热搜' in gmgn_card(NOW,'hot')


def test_bridge_gets_only_workbench_key_and_no_private_key(monkeypatch,isolated):
    from types import SimpleNamespace
    monkeypatch.setattr(g,'api_key',lambda:'gmgn_test_workbench_key')
    monkeypatch.setattr(g.shutil,'which',lambda name:'C:/npm/gmgn-cli.cmd' if name!='node' else 'C:/node.exe')
    monkeypatch.setenv('GMGN_PRIVATE_KEY','unrelated-private-key')
    monkeypatch.setenv('GMGN_DEBUG','1')
    seen={}
    def run(args,**kwargs):
        seen.update(args=args,**kwargs)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'rank':[]}}))
    monkeypatch.setattr(g.subprocess,'run',run)
    assert g.Client().query(['market','trending','--chain','sol'])=={'rank':[]}
    assert seen['env']['GMGN_API_KEY']=='gmgn_test_workbench_key'
    assert 'GMGN_PRIVATE_KEY' not in seen['env'] and 'GMGN_DEBUG' not in seen['env']
    assert 'gmgn_test_workbench_key' not in str(seen['args'])+seen['input']
    assert json.loads(seen['input'])=={'command':'trending','chain':'sol'}
    params=[dict(chain=c,interval='5m',filters=[],limit=500) for c in g.CHAINS]
    g.Client().query(['market','hot-searches','--params',json.dumps(params)])
    assert json.loads(seen['input'])=={'command':'hot-searches','hot_interval':'5m'}


def test_user_screenshot_sort_volume_beats_search_heat_and_24h_data(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    class ScreenshotClient(FakeClient):
        def query(self,args):
            result=super().query(args)
            if args[1]=='hot-searches':
                sample=result[0]['tokens'][0]
                result[0]['tokens']=[sample|dict(symbol='4AGENT',volume=1000,visiting_count=999999),
                                     sample|dict(symbol='BTC',volume=95500,visiting_count=403),
                                     sample|dict(symbol='UNKNOWN_VOLUME',volume=None,visiting_count=1e10)]
                result.append(dict(chain='sol',interval='24h',tokens=[sample|dict(symbol='WRONG_WINDOW',volume=1e12)]))
            return result
    g.Monitor(ScreenshotClient()).cycle()
    snapshot=store.get('gmgn_market')
    assert snapshot['hot']['symbol']=='BTC'
    assert snapshot['hot']['volume']==95500
    assert snapshot['hot_scope']['interval']=='5m'
    assert snapshot['hot_scope']['order_by']=='volume'
    assert snapshot['hot_leaders'][0]['symbol']=='BTC'
    assert 'WRONG_WINDOW' not in [t['symbol'] for t in snapshot['hot_leaders']]


def test_hot_volume_does_not_need_visiting_count(isolated,monkeypatch):
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    class VolumeClient(FakeClient):
        def query(self,args):
            result=super().query(args)
            if args[1]=='hot-searches':
                result[0]['tokens'][0].update(symbol='VOLUME_FIRST',volume=1e7,visiting_count=None)
            return result
    g.Monitor(VolumeClient()).cycle()
    assert store.get('gmgn_market')['hot']['symbol']=='VOLUME_FIRST'


def test_rate_limit_honors_reset_without_immediate_retry(monkeypatch,isolated):
    from types import SimpleNamespace
    monkeypatch.setattr(g,'api_key',lambda:'gmgn_test_key')
    monkeypatch.setattr(g.shutil,'which',lambda name:'C:/npm/gmgn-cli.cmd')
    monkeypatch.setattr(g.time,'time',lambda:NOW)
    calls=[]
    def run(*args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1,stdout=json.dumps({'ok':False,'error':{'status':429,'code':'RATE_LIMIT_BANNED','reset_at':NOW+120}}))
    monkeypatch.setattr(g.subprocess,'run',run)
    with pytest.raises(g.QueryError) as error:
        g.Client().query(['market','hot-searches'])
    assert error.value.delay==122
    assert len(calls)==1


@pytest.mark.parametrize('limited',[False,True])
def test_pause_is_measured_after_scan_or_rate_limit_error(monkeypatch,isolated,limited):
    stamp=[NOW]
    monkeypatch.setattr(g.time,'time',lambda:stamp[0])
    class Monitor:
        def cycle(self):
            stamp[0]+=30  # A slow scan must not consume the pause.
            if limited: raise g.QueryError('GMGN 限频',120)
    monkeypatch.setattr(g,'Monitor',Monitor)
    class Stop(Exception): pass
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds)
        raise Stop()
    monkeypatch.setattr(g.time,'sleep',sleep)
    with pytest.raises(Stop): g.run()
    assert sleeps==[120 if limited else 15]
