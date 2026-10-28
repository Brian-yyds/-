import json
from streamlit.testing.v1 import AppTest
import store
import sftp_sync as sync


def sync_page():
    from sync_ui import render
    render()


def test_settings_validation_host_verification_and_manual_capture(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'ui.sqlite')
    monkeypatch.setattr(sync,'public_key',lambda:'ssh-ed25519 dGVzdA== test-only')
    candidate=dict(host='127.0.0.1',port=22,host_key_type='ssh-ed25519',host_key_base64='dGVzdA==',fingerprint=sync.fingerprint('dGVzdA=='))
    store.put('sync_config',sync.DEFAULTS|dict(host='127.0.0.1'))
    store.put('sync_host_candidate',candidate)
    calls=[]
    monkeypatch.setattr(sync,'upload',lambda *args:calls.append(args))
    monkeypatch.setattr(sync,'probe_host',lambda *args:(_ for _ in ()).throw(AssertionError('No background UI probing')))
    app=AppTest.from_function(sync_page,default_timeout=30).run()
    assert not app.exception
    next(x for x in app.checkbox if x.label=='启用定时上传').set_value(True)
    next(x for x in app.button if x.label=='保存同步设置').click().run()
    assert any('先保存地址并核验' in x.value for x in app.error)
    assert not sync.configuration()['enabled']
    next(x for x in app.text_input if x.label=='从服务器控制台核实的 SHA256 指纹').set_value('SHA256:wrong')
    next(x for x in app.button if x.label=='核验并保存服务器身份').click().run()
    assert any('指纹不一致' in x.value for x in app.error)
    assert not sync.configuration()['host_key_base64']
    next(x for x in app.text_input if x.label=='从服务器控制台核实的 SHA256 指纹').set_value(candidate['fingerprint'])
    next(x for x in app.button if x.label=='核验并保存服务器身份').click().run()
    assert sync.configuration()['host_key_base64']=='dGVzdA=='
    next(x for x in app.number_input if x.label=='发送间隔（秒）').set_value(120)
    next(x for x in app.checkbox if x.label=='启用定时上传').set_value(True)
    next(x for x in app.button if x.label=='保存同步设置').click().run()
    assert not app.exception
    assert sync.configuration()['enabled']
    next(x for x in app.button if x.label=='立即打包 / 加入发送队列').click().run()
    assert store.get('sync_capture_request')
    assert not calls  # UI only sends a local worker request.
