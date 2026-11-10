from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy_feed as f
import copy_trader as source

NOW=1794320000


def lead(i,kind='PUBLIC'):
    return dict(leadPortfolioId=str(5000000000000000000+i),nickname=f'Trader {i}',
                roi=10000-i,pnl=100000-i,portfolioType=kind)


def response(data,status=200):
    return SimpleNamespace(status_code=status,headers={},json=lambda:data)


def rank_session(repeated=False):
    calls=[]
    def post(url,**kwargs):
        body=kwargs['json'];calls.append(body)
        if url==f.RANK_URL:
            page=1 if repeated else body['pageNumber']
            offset=0 if body['dataType']=='ROI' else 25
            rows=[lead(offset+i,'PRIVATE' if offset+i==0 else 'PUBLIC') for i in range((page-1)*30,page*30)]
            return response(dict(code='000000',data=dict(total=120,list=rows)))
        return response(dict(code='000000',data=dict(list=[dict(symbol='ETHUSDT',side='SELL',
            positionSide='LONG',avgPrice='2222.10',executedQty='1',orderTime=(NOW-50)*1000,
            orderUpdateTime=(NOW-40)*1000)])))
    return SimpleNamespace(post=post,calls=calls)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(f,'REQUEST_GAP',0)
    monkeypatch.setattr(f.Monitor,'_smart_step',lambda *a:None)


def test_two_real_pages_each_fifty_overlap_dedup_and_private_excluded(tmp_path):
    m=f.Monitor(tmp_path/'feed.sqlite');s=rank_session()
    assert m.step(s,NOW)=='rank:ROI'
    assert m.step(s,NOW+1)=='rank:PNL'
    snap=m.snapshot()
    assert all(len(r['rows'])==50 for r in snap['rankings'].values())
    assert [c['pageNumber'] for c in s.calls]==[1,2,1,2]
    assert all(c['pageSize']==30 and c['timeRange']=='30D' for c in s.calls)
    members=m.members(snap)
    assert len(members)==87  # 75 union members + the 12 verified screenshot IDs.
    overlap=members[str(5000000000000000025)]
    assert overlap['roi_rank']==26 and overlap['pnl_rank']==1
    assert m.step(s,NOW+2)=='history:'+source.PORTFOLIO
    assert m.step(s,NOW+3)=='history:'+f.curated_traders.TRADERS[1][0]
    assert not any(c.get('portfolioId')==str(5000000000000000000) for c in s.calls)
    assert m.snapshot()['histories'][source.PORTFOLIO]['rows'][0]['direction']=='平多'
    view=f.ui_snapshot(m.snapshot(),NOW+4)
    assert view['items'][0]['notional_display']=='2,222.10'
    assert view['items'][0]['quantity']=='1'
    restored=f.Monitor(m.path)
    assert restored.snapshot()['histories']==m.snapshot()['histories']


def test_repeated_second_page_preserves_last_complete_ranking(tmp_path):
    m=f.Monitor(tmp_path/'feed.sqlite')
    m.step(rank_session(),NOW)
    good=m.snapshot()['rankings']['ROI']['rows']
    assert m.step(rank_session(repeated=True),NOW+901)=='rank:ROI'
    failed=m.snapshot()['rankings']['ROI']
    assert failed['rows']==good and failed['updated']==NOW
    assert '覆盖不足' in failed['error']


def test_rate_limit_stops_all_queries_and_survives_restart(tmp_path):
    m=f.Monitor(tmp_path/'feed.sqlite')
    session=SimpleNamespace(post=lambda *a,**k:response({},429))
    m.step(session,NOW)
    assert m.snapshot()['blocked_until']==NOW+900
    restored=f.Monitor(m.path)
    assert restored.step(SimpleNamespace(post=lambda *a,**k:pytest.fail('queried during limit')),NOW+10)=='blocked'


def test_failed_orders_keep_history_and_ui_marks_delayed(tmp_path,monkeypatch):
    m=f.Monitor(tmp_path/'feed.sqlite');s=rank_session()
    m.step(s,NOW);m.step(s,NOW+1);m.step(s,NOW+2)
    prior=m.snapshot()['histories'][source.PORTFOLIO]
    def fail(*args,**kwargs):raise source.SourceError('未提供公开记录')
    monkeypatch.setattr(source,'fetch',fail)
    m.step(s,NOW+33)
    state=m.snapshot()['histories'][source.PORTFOLIO]
    assert state['rows']==prior['rows'] and state['updated']==prior['updated']
    view=f.ui_snapshot(m.snapshot(),NOW+34)
    assert view['items'][0]['delayed'] and view['errors']==1


def test_ui_keeps_only_current_members_and_seven_day_window(tmp_path):
    m=f.Monitor(tmp_path/'feed.sqlite');s=rank_session()
    m.step(s,NOW);m.step(s,NOW+1);m.step(s,NOW+2)
    snap=m.snapshot();old=dict(snap['histories'][source.PORTFOLIO]['rows'][0])
    snap['histories']['9999999999999999999']=dict(rows=[old],updated=NOW)
    snap['histories'][source.PORTFOLIO]['rows'].append(dict(old,time=(NOW-8*86400)*1000))
    view=f.ui_snapshot(snap,NOW+10)
    assert len(view['items'])==1
    assert view['items'][0]['trader_id']==source.PORTFOLIO
    assert view['private']==1 and view['fresh']==1


def test_combine_never_silently_pads_or_accepts_missing_page():
    rows=[dict(id=str(i)) for i in range(30)]
    with pytest.raises(source.SourceError):f.combine_pages(rows,[],100)
    with pytest.raises(source.SourceError):f.combine_pages(rows,rows,100)
    assert f.combine_pages(rows[:4],[],4)[-1]['rank']==4


def test_selected_identity_and_no_same_name_substitution(tmp_path):
    view=f.ui_snapshot(f.Monitor(tmp_path/'feed.sqlite').snapshot(),NOW)
    ids={r['id'] for r in view['curated']}
    assert len(ids)==12 and '4751838302089254401' in ids
    assert '4794150057005208320' not in ids
    assert {'4925133974869249281','5159399805711344897'}<=ids


def test_smart_mapping_requires_explicit_id_public_flags_and_readable_open(tmp_path):
    m=f.Monitor(tmp_path/'feed.sqlite');s=rank_session()
    m.step(s,NOW);m.step(s,NOW+1);m.step(s,NOW+2)
    snap=m.snapshot();pid=source.PORTFOLIO;tid='5284218613085827584'
    snap['smart']=dict(rows=[dict(id=tid,name='Other namespace',rank=1,pnl=1,roi=2)],
        profiles={tid:dict(public=True,portfolio_id=pid)},updated=NOW)
    assert not f.ui_snapshot(snap,NOW+10)['smart']['trackable']  # only closing record
    snap['histories'][pid]['rows'][0]['direction']='开多'
    view=f.ui_snapshot(snap,NOW+10)
    assert len(view['smart']['trackable'])==1 and view['items'][0]['smart_id']==tid
    assert view['items'][0]['selected']
    snap['smart']['profiles'][tid]['public']=False
    view=f.ui_snapshot(snap,NOW+10)
    assert not view['smart']['trackable'] and view['items'][0]['smart_id'] is None
