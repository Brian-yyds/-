"""Identify tokenized equities for GMGN alarms, never by ticker or chain alone."""
from functools import lru_cache
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests
import store

BACKPACK = 'https://wallet.backpack.exchange/tokenized-stocks/assets'
ROBINHOOD = 'https://api.robinhood.com/rhj/assets'
PLATFORMS = {'xstocks', 'xstock', 'flap_stocks', 'backpack_securities', 'robinhood_stock', 'robinhood_stocks'}
CHAIN_IDS = {4663:'robinhood', 56:'bsc', 8453:'base'}
_cache = None
_cache_until = 0
_cache_db = ''


def address_key(chain, address):
    return chain + ':' + (address.lower() if chain != 'sol' else address)


@lru_cache(maxsize=1)
def seed_registry():
    path = Path(__file__).with_name('gmgn_equity_registry.json')
    return json.loads(path.read_text(encoding='utf-8')).get('addresses', {}) if path.exists() else {}


def registry():
    global _cache, _cache_until, _cache_db
    if _cache is None or _cache_db != str(store.DB) or time.monotonic() >= _cache_until:
        _cache = seed_registry() | store.get('gmgn_equity_registry', {}).get('addresses', {})
        _cache_until = time.monotonic()+30
        _cache_db = str(store.DB)
    return _cache


def equity_reason(token, chain=None):
    chain = chain or token.get('chain', '')
    address = str(token.get('address') or '')
    issuer = registry().get(address_key(chain,address))
    if issuer:
        return 'issuer_registry:' + issuer
    for key in ('launchpad','launchpad_platform'):
        platform = re.sub(r'[\s-]+', '_', str(token.get(key) or '').strip().lower())
        if platform in PLATFORMS:
            return 'platform:' + platform
    name = str(token.get('name') or '').strip()
    # Issuer suffixes are a fallback for new listings while catalogs refresh.
    # Do not block an entire chain or a symbol such as GME/TSLA, used by memes too.
    if chain == 'robinhood' and re.search(r'(?:[•·|\-]\s*)Robinhood Token\s*$', name, re.I):
        return 'issuer_name:Robinhood Token'
    if chain == 'sol' and re.search(r'(?:[•·|\-]\s*)Backpack Securities\s*$', name, re.I):
        return 'issuer_name:Backpack Securities'
    if re.search(r'\bxStocks?\b|\btokeni[sz]ed (?:stock|equity|ETF)\b', name, re.I):
        return 'security_name'
    return ''


class BackpackAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.addresses = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href','')
        parsed = urlsplit(href)
        if parsed.hostname == 'solscan.io':
            found = re.fullmatch(r'/token/([1-9A-HJ-NP-Za-km-z]{32,44})/?', parsed.path)
            if found:
                self.addresses[address_key('sol',found[1])] = 'Backpack'


def parse_backpack(text):
    parser = BackpackAssets()
    parser.feed(text)
    if not parser.addresses:
        raise ValueError('Backpack catalog missing addresses')
    return parser.addresses


def parse_robinhood(data):
    addresses = {}
    for asset in data.get('assets', []):
        for deployment in asset.get('deployments', []):
            chain = CHAIN_IDS.get(deployment.get('chainId'))
            address = deployment.get('contractAddress', '')
            if chain and re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
                addresses[address_key(chain,address)] = 'Robinhood'
    if not addresses:
        raise ValueError('Robinhood catalog missing supported deployments')
    return addresses


def refresh_catalogs(now=None):
    """Two public issuer reads per day; failures keep the last good classifications."""
    global _cache_until
    now = time.time() if now is None else now
    previous = store.get('gmgn_equity_registry', {})
    if now < previous.get('retry_at',0):
        return
    addresses = seed_registry() | previous.get('addresses', {})
    sources = dict(previous.get('sources', {}))
    with requests.Session() as session:
        # Reuse the configured Windows/environment proxy without changing it.
        from gmgn_monitor import system_proxy
        proxy = system_proxy() if not any(session.proxies.values()) else ''
        if proxy and not any(os.environ.get(k) for k in ('HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy')):
            session.proxies.update(http=proxy,https=proxy)
        for issuer, url, parser in [('Backpack',BACKPACK,lambda r:parse_backpack(r.text)),
                                    ('Robinhood',ROBINHOOD,lambda r:parse_robinhood(r.json()))]:
            try:
                response = session.get(url, timeout=(5,8))
                response.raise_for_status()
                if len(response.content)>4*1024*1024:
                    raise ValueError('Issuer catalog too large')
                rows = parser(response)
                addresses.update(rows)
                sources[issuer] = dict(updated_at=now,count=len(rows),url=url,ok=True)
            except (requests.RequestException,ValueError,TypeError,KeyError,AttributeError):
                sources[issuer] = sources.get(issuer, {}) | dict(url=url,ok=False)
    success = all(sources.get(name,{}).get('ok') for name in ('Backpack','Robinhood'))
    result = dict(addresses=addresses,sources=sources,retry_at=now+(86400 if success else 3600))
    store.put('gmgn_equity_registry', result)
    _cache_until = 0
    return result
