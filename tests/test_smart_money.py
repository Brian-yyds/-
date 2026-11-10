from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import smart_money as s
import copy_feed as f
import copy_trader as source


def session(value,status=200):
    return SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(status_code=status,headers={},json=lambda:value))


def test_privacy_flags_are_independent_and_no_nickname_mapping():
    tid='5284218613085827584';row=dict(id=tid,name='熬鹰资本')
    profile=dict(topTraderId=tid,enable=True,sharingPosition=True,sharingLatestRecord=False,
                 futuresCopyTradePortfolioId=source.PORTFOLIO)
    assert not s.profile(session(dict(code='000000',data=profile)),row)['portfolio_id']
    profile['sharingLatestRecord']=True
    assert s.profile(session(dict(code='000000',data=profile)),row)['portfolio_id']==source.PORTFOLIO
    del profile['futuresCopyTradePortfolioId']
    result=s.profile(session(dict(code='000000',data=profile)),row)
    assert result['public'] and not result['portfolio_id'] and '授权' in result['reason']


def test_rank_units_and_access_restriction():
    raw=dict(topTraderId='5284218613085827584',traderName='Public',roi=10.5,pnl=100)
    rows,total=s.rank_page(session(dict(code='000000',data=dict(rows=[raw],total=1))),1)
    assert rows[0]['roi']==1050 and total==1
    with pytest.raises(source.SourceError) as exc:s.rank_page(session({},429),1)
    assert exc.value.cooldown>=900


def test_smart_duplicate_page_preserves_complete_snapshot(tmp_path,monkeypatch):
    m=f.Monitor(tmp_path/'test.sqlite');prior=m.snapshot()
    prior['smart']['rows']=[dict(id='old')]
    monkeypatch.setattr(f,'REQUEST_GAP',0)
    monkeypatch.setattr(s,'rank_page',lambda *a:([dict(id=str(i)) for i in range(20)],100))
    assert m._smart_step(None,100,prior)=='smart:rank'
    assert m.snapshot()['smart']['rows']==[dict(id='old')]
    assert '覆盖不足' in m.snapshot()['smart']['error']
