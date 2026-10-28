"""SFTP configuration belongs on the settings page, not the live board."""
import time
import uuid
import streamlit as st
import store
import sftp_sync as sync


def render():
    st.subheader('采集与定时上传')
    st.caption('网站照常监控，同时整理标准 JSON。包含全市场行情、4小时榜单、重点、币安/GMGN警报、30日成交统计及数据时间；不包含密钥和本机数据库。')
    cfg = sync.configuration()
    show_status()
    with st.form('sftp_settings'):
        a, b = st.columns(2)
        host = a.text_input('SFTP 服务器 IP / 域名', value=cfg['host'])
        port = b.number_input('SSH 端口', min_value=1, max_value=65535, value=int(cfg['port']))
        username = a.text_input('SFTP 用户名', value=cfg['username'])
        directory = b.text_input('上传目录', value=cfg['directory'])
        interval = a.number_input('发送间隔（秒）', min_value=30, max_value=86400, value=int(cfg['interval_seconds']), step=30)
        enabled = b.checkbox('启用定时上传', value=cfg['enabled'])
        st.caption('每包先写隐藏的 .tmp 文件，上传完整后改名为 .json。失败按10秒到10分钟退避重试，电脑重启后继续；不删除待传包。')
        if st.form_submit_button('保存同步设置', type='primary'):
            try:
                updated = sync.validate(cfg | dict(host=host, port=port, username=username,
                                                   directory=directory, interval_seconds=interval, enabled=enabled))
                if (updated['host'], updated['port']) != (cfg['host'], cfg['port']):
                    updated.update(host_key_type='', host_key_base64='')
                if enabled and not updated['host_key_base64']:
                    raise ValueError('请先保存地址并核验下方服务器指纹，再启用上传')
                if enabled and not sync.public_key():
                    raise ValueError('请先生成并在服务器安装下方采集器公钥')
                store.put('sync_config', updated)
                st.success('已保存，后台自动应用。')
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    a, b = st.columns(2)
    if a.button('测试密钥登录与目录', disabled=not cfg['host_key_base64']):
        try:
            with st.spinner('测试中…'):
                st.success(sync.check_connection(cfg))
        except Exception as error:
            st.error(sync.safe_error(error))
    if b.button('立即打包 / 加入发送队列'):
        store.put('sync_capture_request', uuid.uuid4().hex)
        st.info('已通知后台；未启用上传时仅更新本机数据包。')
    with st.expander('SSH 公钥与服务器身份', expanded=not bool(cfg['host_key_base64'])):
        identity_settings(cfg)


def identity_settings(cfg):
    st.subheader('采集器 SSH 公钥')
    st.caption('将此公钥加入服务器 monitor 用户的 authorized_keys。Windows 私钥使用当前系统账户加密，仅留在本机；不读取现有 SSH Agent 或其他钱包密钥。')
    if not sync.public_key():
        if st.button('生成专用 ed25519 密钥'):
            try:
                sync.generate_key()
                st.rerun()
            except Exception as error:
                st.error(sync.safe_error(error))
    else:
        st.code(sync.public_key(), language=None)
        st.download_button('下载公钥', sync.public_key()+'\n', file_name='collector-ed25519.pub', mime='text/plain')
    st.subheader('服务器身份核验')
    if cfg['host_key_base64']:
        st.caption('已固定的指纹：' + sync.fingerprint(cfg['host_key_base64']))
    else:
        st.caption('首次连接先读取指纹，再与服务器管理员或控制台提供的指纹核对。读取指纹不会登录或上传。')
    if st.button('读取服务器指纹', disabled=not cfg['host']):
        try:
            with st.spinner('读取中…'):
                st.session_state['sftp_candidate'] = sync.probe_host(cfg)
        except Exception as error:
            st.error(sync.safe_error(error))
    candidate = st.session_state.get('sftp_candidate', store.get('sync_host_candidate'))
    if candidate and (candidate['host'], candidate['port']) == (cfg['host'], cfg['port']):
        st.code(candidate['fingerprint'], language=None)
        expected = st.text_input('从服务器控制台核实的 SHA256 指纹', placeholder='SHA256:…')
        if st.button('核验并保存服务器身份'):
            if expected.strip() != candidate['fingerprint']:
                st.error('指纹不一致，未保存。')
            else:
                store.put('sync_config', cfg | {k:candidate[k] for k in ('host_key_type','host_key_base64')})
                st.success('服务器身份已固定；现在可测试连接并启用上传。')
                st.rerun()


@st.fragment(run_every='5s')
def show_status():
    health = store.get('sync_health', {})
    st.subheader('同步状态')
    states = dict(local_only='仅本机采集，上传未启用', queued='已打包等待上传', sent='上传成功',
                  retrying='发送失败，等待自动重试', queue_full='待传队列已满',
                  destination_changed='目标已改变，旧队列暂停发送', error='同步异常', waiting='等待下一次发送')
    st.write(states.get(health.get('state'), '等待采集服务启动'))
    now = time.time()
    for field, label in [('last_capture_at','最近打包'), ('last_success_at','最近上传成功')]:
        stamp = health.get(field)
        st.caption(label+'：'+(f'{max(0, now-stamp):.0f}秒前' if stamp else '尚无记录'))
    st.caption(f"待传 {health.get('pending',0)} 包 · {health.get('pending_bytes',0)/1048576:.2f} MB · 连续失败 {health.get('consecutive_failures',0)} 次")
    if health.get('heartbeat') and now-health['heartbeat'] > 30:
        st.warning('同步后台心跳已过期；请重新启动工作台。')
    if health.get('error') and health.get('state') not in ('sent', 'local_only'):
        st.warning(health['error'])
    failed = [k for k, value in health.get('module_status', {}).items() if value != 'ok']
    if failed:
        st.warning('本包部分模块不可用：' + '、'.join(failed))
    path = store.USER / 'sync' / 'latest.json'
    if path.exists():
        st.download_button('下载最新采集 JSON', path.read_bytes(), file_name='monitor-latest.json', mime='application/json')
    st.caption('连续失败写入本机 user_data/sync.log，日志自动轮换；待传队列位于 user_data/sync/outbox.sqlite。原有监控与桌面启动器继续使用。')
