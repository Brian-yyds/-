"""News tests use temporary storage and fake HTTP responses, never real keys."""
import json
import pytest
import store
import data_export
import blockbeats_monitor as news
import blockbeats_ui

NOW=1793232000.


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'news.sqlite')
    monkeypatch.setattr(news.time,'time',lambda:NOW)


def raw(ident=1,**extra):
    return dict(id=ident,title='市场快讯',content='<p>第一段</p><p>第二段</p>',
        link=f'https://m.theblockbeats.info/flash/{ident}',create_time='2026-10-29 12:00:00')|extra


class FakeClient:
    def __init__(self,rows=None):self.rows=rows or [news.normalize(raw(),NOW)];self.calls=[];self.error=None
    def page(self,page):
        self.calls.append(page)
        if self.error:raise self.error
        return self.rows


def test_poll_every_five_minutes_survives_restart_and_deduplicates(isolated):
    client=FakeClient();news.Monitor(client).step(NOW)
    assert client.calls==[1] and len(news.recent())==1
    assert news.recent()[0]['content']=='第一段\n第二段'
    news.Monitor(client).step(NOW+299)
    assert client.calls==[1]
    client.rows=[news.normalize(raw(content='<p>修订内容</p>'),NOW+300)]
    news.Monitor(client).step(NOW+300)
    assert len(client.calls)==2 and len(news.recent())==1
    assert news.recent()[0]['content']=='修订内容'


def test_failure_keeps_cache_and_applies_backoff(isolated):
    client=FakeClient();monitor=news.Monitor(client);monitor.step(NOW)
    client.error=news.NewsError('律动接口限流，等待重试',900)
    monitor.step(NOW+300)
    health=store.get('blockbeats_health')
    assert health['last_success']==NOW and health['next_poll_at']==NOW+900
    assert len(news.recent())==1
    monitor.step(NOW+899)
    assert len(client.calls)==2
    client.error=None;monitor.step(NOW+900)
    assert store.get('blockbeats_health')['error']==''


def test_disabled_worker_and_ui_never_request_network(isolated,monkeypatch):
    store.put('blockbeats_config',dict(enabled=False))
    client=FakeClient();news.Monitor(client).step(NOW)
    assert not client.calls and store.get('blockbeats_health')['enabled'] is False
    monkeypatch.setattr(news.Client,'page',lambda *a:(_ for _ in ()).throw(AssertionError('UI cannot fetch')))
    assert '律动快讯' in blockbeats_ui.frame_html()


def test_recovery_pages_are_bounded_and_flag_incomplete_backfill(isolated):
    news.Monitor(FakeClient()).step(NOW)
    class Recovery(FakeClient):
        def page(self,page):
            self.calls.append(page)
            return [news.normalize(raw(i),NOW) for i in range(300-(page-1)*50,300-page*50,-1)]
    client=Recovery();news.Monitor(client).step(NOW+300)
    assert client.calls==[1,2,3]
    assert store.get('blockbeats_health')['coverage_limited'] is True
    assert len(news.recent(500))==151


def test_html_is_plain_text_and_links_are_official_only():
    item=news.normalize(raw(content='<script>bad()</script><p>你好<b>世界</b></p><style>bad</style>',link='javascript:alert(1)'),NOW)
    assert item['content']=='你好世界' and item['link']==''
    assert not news.official_link('https://theblockbeats.info.evil.test/flash/1')
    assert not news.official_link('https://user:pass@www.theblockbeats.info/flash/1')
    assert not news.official_link('')
    assert news.official_link('http://m.theblockbeats.info/flash/1')=='https://m.theblockbeats.info/flash/1'
    assert news.normalize(raw(create_time='unknown'),NOW)['published_at'] is None
    assert not news.normalize(raw(id=True),NOW)


def test_stable_frame_escapes_script_payload_and_never_contains_key(isolated):
    news.Monitor(FakeClient([news.normalize(raw(title='</script><script>bad()</script>新闻'),NOW)])).step(NOW)
    store.put('blockbeats_config',dict(api_key='PRIVATE_NEWS_KEY'))
    before=blockbeats_ui.frame_html()
    health=store.get('blockbeats_health');store.put('blockbeats_health',health|dict(heartbeat=NOW+15))
    assert before==blockbeats_ui.frame_html()  # Clock ticks must not reset the iframe's scroll.
    assert 'PRIVATE_NEWS_KEY' not in before and '</script><script>bad()' not in before
    payload=before.split('<script type="application/json" id="news-data">')[1].split('</script>')[0]
    assert json.loads(payload)['items'][0]['title']=='新闻'


def test_export_is_allowlisted_and_contains_news_timestamps(isolated):
    news.Monitor(FakeClient()).step(NOW)
    store.put('blockbeats_config',dict(api_key='PRIVATE_NEWS_KEY'))
    packet=data_export.build_packet('test',now=NOW)
    module=packet['modules']['blockbeats_news']
    assert module['status']=='ok' and module['data']['poll_seconds']==300
    assert module['data']['items'][0]['published_text']=='2026-10-29 12:00:00'
    assert 'PRIVATE_NEWS_KEY' not in data_export.encode(packet).decode()


class Response:
    def __init__(self,status=200,headers=None):
        self.status_code=status;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield json.dumps(dict(status=0,data=dict(page=1,data=[raw()]))).encode()


def test_https_header_only_credentials_and_redirects_disabled(monkeypatch):
    from types import SimpleNamespace
    seen={};client=news.Client()
    def get(url,**kwargs):seen.update(url=url,**kwargs);return Response()
    client.session=SimpleNamespace(get=get)
    monkeypatch.setattr(news,'api_key',lambda:'test-only-news-key')
    assert client.page(1)[0]['id']==1
    assert seen['url']=='https://api-pro.theblockbeats.info/v1/newsflash'
    assert seen['headers']=={'api-key':'test-only-news-key'}
    assert seen['params']==dict(page=1,size=50,lang='cn') and seen['allow_redirects'] is False
    client.session=SimpleNamespace(get=lambda *a,**kw:Response(429,{'Retry-After':'900'}))
    with pytest.raises(news.NewsError) as error:client.page(1)
    assert error.value.wait==900
