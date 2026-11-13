from pathlib import Path
from streamlit.testing.v1 import AppTest
import store


def test_server_page_uses_three_alarm_panels_without_local_collectors(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'isolated.sqlite')
    path=Path(__file__).resolve().parents[1]/'deploy/server_app.py'
    app=AppTest.from_file(str(path),default_timeout=30).run()
    assert not app.exception
    text=''.join(str(x.value) for x in app.markdown)
    assert all(name in text for name in ('GMGN 警报','Web3 钱包信号','特级警报'))
    assert all(name not in text for name in ('上涨榜','下跌榜','律动快讯','重点监控'))
