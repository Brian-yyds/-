"""GMGN read-only credential, encrypted for the signed-in Windows user."""
import base64
import os
import store


def save_key(key):
    from ai_analysis import _protect
    if not key.strip().startswith('gmgn_'):
        raise ValueError('GMGN Key 格式不正确')
    store.USER.mkdir(exist_ok=True)
    path = store.USER / 'api_keys.gmgn.dpapi'
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(base64.b64encode(_protect(key.strip().encode())))
    tmp.replace(path)


def api_key():
    key = os.environ.get('GMGN_API_KEY', '').strip()
    if key:
        return key
    path = store.USER / 'api_keys.gmgn.dpapi'
    if not path.exists():
        return ''
    from ai_analysis import _protect
    return _protect(base64.b64decode(path.read_bytes()), True).decode()


if __name__ == '__main__':
    from getpass import getpass
    save_key(getpass('GMGN API Key (hidden): '))
    print('GMGN Key 已加密保存在本机。')
