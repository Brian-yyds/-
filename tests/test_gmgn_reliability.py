import pytest
import store
import gmgn_monitor as g

NOW=1793232000.


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'gmgn.sqlite')
    clock=[NOW]
    monkeypatch.setattr(g.time,'time',lambda:clock[0])
    return clock


def raw(i,volume=200001):
    return dict(address='0x'+f'{i:040x}',symbol='TEST',name='Test',volume=volume,renowned_count=2,
                launchpad='fourmeme',launchpad_platform='FourMeme',launchpad_status='1',open_timestamp=NOW-60)


def test_fifty_is_enough_when_tail_cannot_qualify_and_equality_is_not_capped(isolated):
    class Client:
        def __init__(self):self.calls=[]
        def query(self,args):self.calls.append(args);return dict(rank=[raw(i,150000) for i in range(1,51)])
    client=Client();tokens,coverage=g.Monitor(client).candidates('bsc',g.DEFAULTS)
    assert len(client.calls)==1 and coverage['capped'] is False
    assert client.calls[0][client.calls[0].index('--limit')+1]=='50'
    assert 'is_out_market' in client.calls[0] and 'is_internal_market' not in client.calls[0]
    assert len(tokens)==50


def test_volume_boundary_supplements_small_page_deduplicates_and_reports_large_ties(isolated):
    class Client:
        def __init__(self,tie=False):self.calls=[];self.tie=tie
        def query(self,args):
            self.calls.append(args)
            if len(self.calls)==1 or self.tie:return dict(rank=[raw(i,200000) for i in range(1,51)])
            assert float(args[args.index('--max-volume')+1])==200000
            return dict(rank=[raw(50,200000)]+[raw(i,140000) for i in range(51,100)])
    client=Client();tokens,c=g.Monitor(client).candidates('bsc',g.DEFAULTS)
    assert len(tokens)==99 and c['pages']==2 and not c['capped']
    client=Client(tie=True);tokens,c=g.Monitor(client).candidates('bsc',g.DEFAULTS)
    assert len(client.calls)==2 and len(tokens)==50 and c['capped']


def test_second_page_failure_still_evaluates_first_page_without_claiming_complete(isolated):
    class Client:
        def query(self,args):
            if args[0]=='token':return []
            chain=args[args.index('--chain')+1]
            if chain!='bsc':return dict(rank=[])
            if '--max-volume' in args:raise g.QueryError('timeout',30,'TIMEOUT')
            return dict(rank=[raw(i) for i in range(1,51)])
    g.Monitor(Client()).cycle()
    state=store.get('gmgn_market')['chains']['bsc']
    assert state['capped'] and state['failures']==1 and state['error_code']=='TIMEOUT'
    assert state['updated']==NOW
    assert len(g.pending_alerts())==50


def test_failed_chain_has_durable_backoff_while_other_chains_keep_scanning(isolated):
    clock=isolated
    class Client:
        def __init__(self):self.calls=[];self.fail=True
        def query(self,args):
            chain=args[args.index('--chain')+1];self.calls.append(chain)
            if chain=='sol' and self.fail:raise g.QueryError('network',30,'ECONNRESET')
            return dict(rank=[])
    client=Client();g.Monitor(client).cycle()
    state=store.get('gmgn_market')['chains']['sol']
    assert state['retry_at']==NOW+30 and state['failures']==1
    clock[0]+=15;client.calls=[];g.Monitor(client).cycle()
    assert client.calls==['bsc','base','robinhood']
    clock[0]=NOW+31;g.Monitor(client).cycle()
    state=store.get('gmgn_market')['chains']['sol']
    assert state['retry_at']==NOW+91 and state['failures']==2
    clock[0]=NOW+92;client.fail=False;g.Monitor(client).cycle()
    state=store.get('gmgn_market')['chains']['sol']
    assert state['failures']==0 and not state['error'] and state['retry_at']==0
    assert store.get('gmgn_market')['last_success']==NOW+92
    assert not g.pending_alerts()


def test_global_rate_limit_still_stops_all_chains(isolated):
    calls=[]
    class Client:
        def query(self,args):
            calls.append(args)
            store.put('gmgn_rate_limit_until',NOW+120)
            raise g.QueryError('quota',122,'RATE_LIMIT_BANNED',429)
    with pytest.raises(g.QueryError):g.Monitor(Client()).cycle()
    assert len(calls)==1
    assert store.get('gmgn_market')['chains']['sol']['error_code']=='RATE_LIMIT_BANNED'
