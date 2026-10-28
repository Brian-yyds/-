"""Dedicated SFTP identity and strict host-key validation; never use an SSH agent."""
import base64
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import socket

import store

DEFAULTS = dict(enabled=False, host='', port=22, username='monitor', directory='/inbox',
                interval_seconds=60, host_key_type='', host_key_base64='')


class SyncError(Exception):
    pass


def configuration():
    return DEFAULTS | store.get('sync_config', {})


def validate(cfg):
    cfg = DEFAULTS | cfg
    host = cfg['host'].strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
        raise ValueError('请填写服务器 IP 或域名，不含协议和路径')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', cfg['username']):
        raise ValueError('SFTP 用户名格式无效')
    directory = cfg['directory'].strip()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]*', directory) or '..' in directory.split('/'):
        raise ValueError('上传目录需使用绝对路径，例如 /inbox，不含空格或上级路径')
    if not 1 <= int(cfg['port']) <= 65535 or not 30 <= int(cfg['interval_seconds']) <= 86400:
        raise ValueError('端口范围1–65535；上传间隔30–86400秒')
    return cfg | dict(host=host, directory=directory.rstrip('/') or '/',
                      port=int(cfg['port']), interval_seconds=int(cfg['interval_seconds']))


def destination(cfg):
    """Bind queued packets to one host, login, directory and pinned server identity."""
    value = [cfg[k] for k in ('host', 'port', 'username', 'directory', 'host_key_type', 'host_key_base64')]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def key_paths():
    root = store.USER / 'sync'
    return root / ('id_ed25519.dpapi' if os.name == 'nt' else 'id_ed25519'), root / 'id_ed25519.pub'


def public_key():
    _, path = key_paths()
    return path.read_text(encoding='ascii').strip() if path.exists() else ''


def generate_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private, public = key_paths()
    if private.exists():
        if not public.exists():
            raise SyncError('私钥已存在但公钥文件缺失；请恢复备份，不会自动覆盖密钥')
        return public_key()
    key = Ed25519PrivateKey.generate()
    secret = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                               serialization.NoEncryption())
    if os.name == 'nt':
        from ai_analysis import _protect
        secret = base64.b64encode(_protect(secret))
    private.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental identity replacement.
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(secret)
    text = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                         serialization.PublicFormat.OpenSSH).decode() + ' workbench-collector'
    public.write_text(text + '\n', encoding='ascii')
    return text


def load_key():
    import paramiko
    private, _ = key_paths()
    if not private.exists():
        raise SyncError('尚未生成采集器 SSH 密钥')
    secret = private.read_bytes()
    if os.name == 'nt':
        from ai_analysis import _protect
        secret = _protect(base64.b64decode(secret), True)
    return paramiko.Ed25519Key.from_private_key(io.StringIO(secret.decode()))


def fingerprint(key_base64):
    try:
        raw = base64.b64decode(key_base64, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('服务器主机公钥格式无效') from error
    return 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')


def probe_host(cfg):
    """Read server identity without authenticating or transferring any user data."""
    import paramiko
    cfg = validate(cfg)
    with socket.create_connection((cfg['host'], cfg['port']), timeout=10) as sock:
        with paramiko.Transport(sock) as transport:
            transport.start_client(timeout=10)
            key = transport.get_remote_server_key()
            return dict(host=cfg['host'], port=cfg['port'], host_key_type=key.get_name(),
                        host_key_base64=key.get_base64(), fingerprint=fingerprint(key.get_base64()))


def connect(cfg):
    import paramiko
    cfg = validate(cfg)
    if not cfg.get('host_key_base64') or not cfg.get('host_key_type'):
        raise SyncError('等待核验服务器主机指纹；不会自动信任未知服务器')
    client = paramiko.SSHClient()
    hostname = cfg['host'] if cfg['port'] == 22 else f"[{cfg['host']}]:{cfg['port']}"
    key = paramiko.PKey.from_type_string(cfg['host_key_type'], base64.b64decode(cfg['host_key_base64']))
    client.get_host_keys().add(hostname, key.get_name(), key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(cfg['host'], port=cfg['port'], username=cfg['username'], pkey=load_key(),
                       allow_agent=False, look_for_keys=False, timeout=10, banner_timeout=10,
                       auth_timeout=10, channel_timeout=15)
        return client
    except Exception:
        client.close()
        raise


def publish(sftp, remote_dir, filename, body):
    """Retry-safe: verify an existing final file; otherwise upload .tmp then rename."""
    final = str(PurePosixPath(remote_dir) / filename)
    temporary = str(PurePosixPath(remote_dir) / ('.' + filename + '.tmp'))
    try:
        existing = sftp.stat(final)
    except OSError as error:
        if error.errno != 2:
            raise
    else:
        if existing.st_size != len(body):
            raise SyncError('远端同名文件与本机数据不一致，已停止覆盖')
        with sftp.open(final, 'rb') as stream:
            remote = stream.read(len(body) + 1)
        if hashlib.sha256(remote).digest() != hashlib.sha256(body).digest():
            raise SyncError('远端同名文件校验不一致，已停止覆盖')
        return 'already_present'
    sftp.putfo(io.BytesIO(body), temporary, file_size=len(body), confirm=True)
    # Standard SFTP rename deliberately refuses to overwrite an existing file.
    sftp.rename(temporary, final)
    return 'uploaded'


def upload(cfg, filename, body):
    with connect(cfg) as client:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(20)
            return publish(sftp, cfg['directory'], filename, body)


def check_connection(cfg):
    with connect(cfg) as client:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(15)
            sftp.listdir_attr(cfg['directory'])
    return 'SSH 密钥登录成功，上传目录可读取；实际写入会在首次上传时验证。'


def safe_error(error):
    import paramiko
    if isinstance(error, SyncError):
        return str(error)
    if isinstance(error, paramiko.BadHostKeyException):
        return '服务器主机公钥发生变化，已阻止连接，请重新核验'
    if isinstance(error, paramiko.AuthenticationException):
        return 'SSH 密钥登录失败，请确认服务器已为此用户安装采集器公钥'
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'SFTP 连接或传输超时，将自动重试'
    if isinstance(error, OSError):
        return f'SFTP 网络或文件操作失败（错误码 {error.errno}），请检查连通性和 /inbox 权限'
    if isinstance(error, ValueError):
        return '同步配置或本机密钥读取失败，请在当前系统账户下检查设置'
    return 'SFTP 连接或传输失败（' + type(error).__name__ + '），将自动重试'
