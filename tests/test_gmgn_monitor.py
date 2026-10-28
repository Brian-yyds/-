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



def test_robinhood_rules_and_links_use_correct_chain(isolated):
    raw=token()|dict(address='0x'+'a'*40)
    rob=g.normalize(raw,'robinhood',NOW)
    assert g.reason(rob,g.DEFAULTS,NOW)=='今日毕业放量'
    assert g.token_url(rob)=='https://gmgn.ai/robinhood/token/0x'+'a'*40
    assert not g.valid_address('rob',rob['address'])



@pytest.mark.parametrize('status',['0',0])
def test_unopened_token_cannot_bypass_rule_with_empty_platform(status):
    assert not g.reason(token(launchpad='',launchpad_platform='',launchpad_status=status,volume=2000000),g.DEFAULTS,NOW)



def test_validated_links_and_read_only_client():
    assert g.token_url(token()).startswith('https://gmgn.ai/sol/token/')
    assert not g.token_url(token()|{'address':'javascript:alert(1)'})
    with pytest.raises(ValueError):
        g.Client().query(['swap','submit'])



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
    params=[dict(chain='sol',address=ADDRESS)]
    g.Client().query(['token','info-batch','--params',json.dumps(params)])
    assert json.loads(seen['input'])=={'command':'info-batch','tokens':params}




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
        g.Client().query(['market','trending','--chain','sol'])
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
