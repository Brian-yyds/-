import json
import os
import time
import pytest
import ai_analysis as ai
import store


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',tmp_path/'ai.sqlite')
    monkeypatch.setattr(store,'USER',tmp_path)


def profile(protocol='OpenAI兼容'):
    return dict(id='test',name='test',protocol=protocol,base_url='https://provider.example/v1',model='a-model',max_tokens=1800)


@pytest.mark.parametrize('protocol,suffix,header',[('OpenAI兼容','/chat/completions','Authorization'),('Anthropic','/messages','x-api-key'),('Gemini',':generateContent','x-goog-api-key')])
def test_provider_request_contracts_keep_secrets_out_of_body(protocol,suffix,header):
    url,headers,body=ai.request_parts(profile(protocol),'test-secret',{'value':2})
    assert url.endswith(suffix) and header in headers
    assert 'test-secret' not in json.dumps(body)
    assert 'test-secret' not in url


def test_credential_in_url_and_remote_http_are_rejected():
    for url in ['http://provider.example/v1','https://key@provider.example/v1','https://provider.example/v1?key=secret']:
        with pytest.raises(ValueError): ai.validate_profile(profile()|{'base_url':url})
    ai.validate_profile(profile()|{'base_url':'http://127.0.0.1:11434/v1'})


def test_dpapi_key_round_trip_and_metadata_has_no_secret(isolated):
    if os.name!='nt': pytest.skip('Windows DPAPI')
    ai.save_profile(profile(),'unit-test-secret')
    assert ai.api_key(profile())=='unit-test-secret'
    assert 'unit-test-secret' not in (store.USER/'api_keys.dpapi.json').read_text()
    assert 'unit-test-secret' not in json.dumps(ai.configuration())
    with pytest.raises(ValueError,match='重新填写'):
        ai.save_profile(profile()|{'base_url':'https://another.example/v1'})


def test_pending_dedupe_daily_budget_and_no_automatic_retry(isolated,monkeypatch):
    ai.save_profile(profile())
    cfg=ai.configuration()|{'daily_limit':1}
    store.put('ai_config',cfg)
    ident=ai.enqueue({'captured_at':time.time(),'row':{'symbol':'X'}},'test')
    with pytest.raises(ValueError): ai.enqueue({'captured_at':time.time()},'test')
    def fail(*args): raise ValueError('模拟接口失败')
    monkeypatch.setattr(ai,'invoke',fail)
    assert ai.work_once()
    assert ai.jobs()[0]['status']=='error'
    assert not ai.work_once()
    with pytest.raises(ValueError,match='上限'): ai.enqueue({'captured_at':time.time()},'test')
    assert ai.jobs()[0]['id']==ident


def test_pending_profile_cannot_be_changed_and_result_preserves_snapshot(isolated,monkeypatch):
    ai.save_profile(profile())
    data={'captured_at':time.time(),'row':{'symbol':'X'}}
    ai.enqueue(data,'test')
    with pytest.raises(ValueError,match='已有任务'): ai.enqueue(data,'test')
    with pytest.raises(ValueError,match='正在处理'): ai.save_profile(profile()|{'model':'other'})
    monkeypatch.setattr(ai,'invoke',lambda *a:('测试分析',{'total_tokens':20}))
    assert ai.work_once()
    result=ai.jobs()[0]
    assert result['snapshot']==data and result['result']=='测试分析'
    assert result['usage']['total_tokens']==20


def test_provider_errors_are_sanitized_and_redirects_disabled(isolated,monkeypatch):
    p=profile()|{'base_url':'http://localhost:9999/v1'}
    class Reply: status_code=401
    def post(*args,**kwargs):
        assert not kwargs['allow_redirects']
        return Reply()
    monkeypatch.setattr(ai.requests,'post',post)
    with pytest.raises(ValueError,match='HTTP 401'): ai.invoke(p,{})


def test_expired_queue_snapshot_does_not_call_provider(isolated,monkeypatch):
    ai.save_profile(profile())
    ai.enqueue({'captured_at':time.time()-200},'test')
    def forbidden(*args): pytest.fail('expired snapshot sent to provider')
    monkeypatch.setattr(ai,'invoke',forbidden)
    ai.work_once()
    assert '超过3分钟' in ai.jobs()[0]['error']


@pytest.mark.parametrize('protocol,response',[
    ('OpenAI兼容',{'choices':[{'message':{'content':'证据复核完成'}}],'usage':{'total_tokens':42}}),
    ('Anthropic',{'content':[{'type':'text','text':'证据复核完成'}],'usage':{'output_tokens':42}}),
    ('Gemini',{'candidates':[{'content':{'parts':[{'text':'证据复核完成'}]}}],'usageMetadata':{'totalTokenCount':42}}),
])
def test_provider_responses_return_text_and_numeric_usage(protocol,response,monkeypatch):
    monkeypatch.setattr(ai,'api_key',lambda p:'test-secret')
    class Reply:
        status_code=200
        def json(self): return response
    monkeypatch.setattr(ai.requests,'post',lambda *a,**k:Reply())
    text,usage=ai.invoke(profile(protocol),{'captured_at':time.time()})
    assert text=='证据复核完成' and 42 in usage.values()
