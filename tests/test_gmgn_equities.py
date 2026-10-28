import json
import time
import pytest
import gmgn_equities as equities
import gmgn_monitor as gmgn
import data_export
import sftp_sync
import store


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'equities.sqlite')
    equities._cache_until=0


def token(**updates):
    now=time.time()
    return dict(chain='sol',address='C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump',symbol='GME',name='Gigantic Milker Enjoyers',
                launchpad='',launchpad_platform='pool_meteora',launch_known=True,launchpad_status='1',
                volume=1000001,renowned_count=3,smart_degen_count=1,open_timestamp=now-60)|updates


@pytest.mark.parametrize('value',[
    dict(chain='sol',address='SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb',name='Unknown'),
    dict(chain='robinhood',address='0xc0d6457c16cc70d6790dd43521c899c87ce02f35',name='Unknown'),
    dict(chain='robinhood',address='0x'+'f'*40,name='New Asset • Robinhood Token'),
    dict(name='New Asset - Backpack Securities'),
    dict(name='Example xStock'),dict(name='Example Tokenized Stock'),
    dict(launchpad='xstocks'),dict(launchpad_platform='flap_stocks'),
])
def test_stock_evidence_blocks_both_alarm_rules(isolated,value):
    item=token(**value)
    assert equities.equity_reason(item)
    if value.get('launchpad') in equities.PLATFORMS:
        item['launchpad_platform']=value['launchpad']  # Keep the evidence while varying the rule branch.
    for launchpad in ('','pump'):
        assert not gmgn.reason(item|dict(launchpad=launchpad),gmgn.DEFAULTS,time.time())


def test_ticker_collision_robinhood_meme_and_usdg_are_not_blanket_blocked(isolated):
    assert not equities.equity_reason(token())
    assert gmgn.reason(token(),gmgn.DEFAULTS,time.time())=='非发射台放量'
    for name,symbol in [('Tesla Meme','TSLA'),('GME','GME'),('New meme','NEW')]:
        item=token(chain='robinhood',address='0x'+'f'*40,name=name,symbol=symbol)
        assert not equities.equity_reason(item)
        assert gmgn.reason(item,gmgn.DEFAULTS,time.time())
    assert not equities.equity_reason(token(chain='robinhood',address='0x5fc5360d0400a0fd4f2af552add042d716f1d168',name='Global Dollar',symbol='USDG'))


def test_existing_stock_history_hidden_without_deleting_and_exported_suppression_advances_cursor(isolated):
    normal=token();stock=token(name='SpaceX - Backpack Securities',address='SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb')
    with store.connect() as con:
        gmgn.schema(con)
        for i,item in enumerate((normal,stock),1):
            con.execute('INSERT INTO gmgn_alerts(id,chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?,?)',
                        (i,item['chain'],item['address'],'非发射台放量',i,i,json.dumps(item)))
    assert [r['id'] for r in gmgn.pending_alerts()]==[1]
    assert gmgn.pending_ids()==[1]
    assert [r['id'] for r in gmgn.recent_alerts()]==[1]
    events,cursor,more=data_export.read_events({})
    assert [r['id'] for r in events['gmgn_alerts']['new']]==[1]
    assert [r['id'] for r in events['gmgn_alerts']['recent']]==[1]
    assert events['gmgn_alerts']['suppressed']==[dict(id=2,chain='sol',address=stock['address'],reason='tokenized_equity')]
    assert cursor['gmgn_alerts']==2
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==2
    gmgn.record_scan('sol',[stock|dict(address='1'*32)],gmgn.DEFAULTS,time.time())
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM gmgn_alerts').fetchone()[0]==2


def test_catalogs_validate_chain_and_malformed_data_does_not_clear_cache(isolated,monkeypatch):
    sol='SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb'
    html=f'<a href="https://solscan.io/token/{sol}">SPCX</a><a href="https://evil.example/token/{sol}">bad</a>'
    assert equities.parse_backpack(html)=={'sol:'+sol:'Backpack'}
    with pytest.raises(ValueError):equities.parse_backpack('<html>Missing table</html>')
    address='0x'+'A'*40
    rows=equities.parse_robinhood(dict(assets=[dict(deployments=[dict(chainId=4663,contractAddress=address),dict(chainId=1,contractAddress=address)])]))
    assert rows=={'robinhood:'+address.lower():'Robinhood'}
    store.put('gmgn_equity_registry',dict(addresses={'base:'+address.lower():'Backpack'},retry_at=0))
    class Response:
        text='<html>not a directory</html>';content=b'invalid'
        def json(self):return {}
        def raise_for_status(self):pass
    calls=[]
    monkeypatch.setattr(equities.requests.Session,'get',lambda *args,**kwargs:calls.append(args) or Response())
    now=time.time();equities.refresh_catalogs(now)
    assert len(calls)==2
    equities.refresh_catalogs(now+1)
    assert len(calls)==2
    assert equities.registry()['base:'+address.lower()]=='Backpack'


def test_current_rules_and_public_contract_keep_alarm_scope_local(isolated):
    assert sftp_sync.DEFAULTS['interval_seconds']==60
    assert gmgn.DEFAULTS['graduate_volume']==150000
    assert gmgn.DEFAULTS['kol_count']==1
    assert gmgn.DEFAULTS['other_volume']==1000000
    store.put('gmgn_config',dict(exclude_equities=False))
    assert gmgn.configuration()['exclude_equities'] is True
    store.put('gmgn_market',dict(hot=token(name='Stock - Backpack Securities')))
    snapshot=data_export.gmgn_data(dict(now=time.time()))
    assert snapshot['rules']['exclude_equities'] is True
    assert snapshot['rules']['smart_money_required'] is False
    assert snapshot['rules']['alarm_time_timezone']=='Asia/Shanghai'
    assert 'people' not in snapshot['rules']
    assert 'hot' not in snapshot and snapshot['watch_ranking']['total']==0
    assert store.settings()['include_stocks'] is True  # Binance scope is unchanged.
