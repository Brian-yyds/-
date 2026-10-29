"""BlockBeats read-only API credential; never include it in public snapshots."""
import base64
import os
import re
import store


def save_key(key):
    from ai_analysis import _protect
    key=key.strip()
    if not re.fullmatch(r'bbp_[A-Za-z0-9]{24,128}',key):
        raise ValueError('律动 API Key 格式不正确')
    store.USER.mkdir(exist_ok=True)
    path=store.USER/'api_keys.blockbeats.dpapi'
    temporary=path.with_suffix('.tmp')
    temporary.write_bytes(base64.b64encode(_protect(key.encode())))
    temporary.replace(path)


def api_key():
    key=os.environ.get('BLOCKBEATS_API_KEY','').strip()
    if key:return key
    path=store.USER/'api_keys.blockbeats.dpapi'
    if not path.exists():return ''
    from ai_analysis import _protect
    return _protect(base64.b64decode(path.read_bytes()),True).decode()


def key_available():
    return bool(os.environ.get('BLOCKBEATS_API_KEY','').strip()) or (store.USER/'api_keys.blockbeats.dpapi').exists()
