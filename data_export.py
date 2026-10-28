"""Versioned public-monitor data contract. Never export the KV store wholesale."""
import json
import math
import time
import uuid

import store

SCHEMA_VERSION = '1.0'
SECTIONS = {}
EVENT_LIMIT = 1000


def section(name, version=1):
    """Future monitors register an explicit, credential-free data adapter here."""
    def decorate(reader):
        if name in SECTIONS:
            raise ValueError('Duplicate export section: ' + name)
        SECTIONS[name] = (version, reader)
        return reader
    return decorate


def fields(value, names):
    return {key: value[key] for key in names.split() if key in value}


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


TOKEN_FIELDS = ('chain address symbol name logo volume market_cap holder_count smart_degen_count '
                'renowned_count open_timestamp creation_timestamp visiting_count launchpad '
                'launchpad_platform launchpad_status launch_known observed asset_class equity_evidence')


def token(value):
    if not isinstance(value, dict):
        return None
    from gmgn_equities import equity_reason
    evidence = equity_reason(value)
    return fields(value, TOKEN_FIELDS) | dict(asset_class='equity' if evidence else 'unclassified',equity_evidence=evidence)


@section('binance_market')
def binance_market(context):
    import monitoring
    cfg = store.settings()
    info = store.get('exchange_info', {})
    live = store.get('live_market', {})
    rows = monitoring.build_rows(cfg, now=context['now'], scope='market')
    row_fields = ('symbol asset_type priority price price_fresh price_time price_change '
                  'price_basis base_time base_price price_span quote minute_ready '
                  'oi oi_change oi_span oi_basis oi_time oi_usdt change24h quote24h mark')
    public_rows = [fields(row, row_fields) for row in rows]
    lookup = {r['symbol']: r for r in public_rows}
    ranks = {kind: [r['symbol'] for r in monitoring.ranked(rows, kind)]
             for kind in ('up', 'down', 'oi_up', 'oi_down')}
    ticks = {s: fields(t, 'price event_time change24h quote24h mark')
             for s, t in live.get('tickers', {}).items()
             if s in info.get('symbols', [])}
    focus = monitoring.focus_quotes(cfg['top_symbols'], now=context['now'])
    return dict(observed_at=live.get('saved_at'), window_minutes=store.RANK_WINDOW_MINUTES,
                quote_currency='USDT', universe=fields(info, 'fetched_at symbols market_symbols alt_symbols stock_symbols commodity_symbols'),
                tickers=ticks, rows=list(lookup.values()), rankings=ranks, focus=focus,
                rules=fields(cfg, 'spike_up_pct spike_down_pct top_symbols priority market_excluded include_stocks'),
                freshness=dict(price_market_max_age_seconds=90, focus_max_age_seconds=20,
                               oi_market_max_age_seconds=180),
                spike_window_minutes=15)


@section('gmgn',version=2)
def gmgn_data(context):
    import gmgn_monitor as gmgn
    snapshot=store.get('gmgn_market',{})
    ranking=gmgn.tracking.ranking_snapshot(context['now'])
    ranking['rows']=[fields(r,'chain address first_alert expires_at last_alert volume_1h volume_observed')|{'token':token(r['token'])} for r in ranking['rows']]
    return dict(observed_at=snapshot.get('last_success'),heartbeat=snapshot.get('heartbeat'),
                retry_at=snapshot.get('retry_at'),enabled=snapshot.get('enabled'),
                chain_status={c:fields(v,'updated count capped') for c,v in snapshot.get('chains',{}).items()},
                chains=list(gmgn.CHAINS),watch_ranking=ranking,
                rules=fields(gmgn.configuration(),'enabled graduate_volume other_volume people kol_count poll_seconds exclude_equities tracking_days daily_limit')|
                      dict(daily_timezone='Asia/Shanghai',tracking_anchor='first_alert',repeat_requires_new_breakout=True))


@section('market_volume')
def market_volume(context):
    import volume_monitor
    snapshot = store.get(volume_monitor.SNAPSHOT_KEY, {})
    days = []
    for day in snapshot.get('days', []):
        days.append(fields(day, 'start quote top10_quote') | {
            'top10': [fields(t, 'symbol quote') for t in day.get('top10', [])]})
    return fields(snapshot, 'complete window_days symbol_count range_start range_end includes_today timezone updated_at scan_started source scope') | {'days': days}


@section('health')
def health(context):
    # Do not forward arbitrary exception strings: they can include private URLs.
    specs = {
        'binance': ('monitor_health', 'heartbeat symbols market_symbols cycle_seconds blocked_until mode'),
        'binance_stream': ('stream_health', 'connected last_message'),
        'market_scan': ('market_health', 'heartbeat total history_seeded round_seconds sampling'),
        'gmgn': ('gmgn_market', 'heartbeat last_success retry_at hot_updated'),
        'volume': ('market_volume_health', 'heartbeat completed total failed blocked_until'),
    }
    return {name: fields(store.get(key, {}), names) for name, (key, names) in specs.items()}


def read_events(cursor):
    """New events are paged durably; recent state also carries confirmations/updates."""
    next_cursor = dict(cursor)
    result = {}
    more = False
    with store.connect() as con:
        con.row_factory = __import__('sqlite3').Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('spike_alerts', 'gmgn_alerts'):
            if table not in tables:
                result[table] = dict(new=[], recent=[], has_more=False)
                continue
            if table=='gmgn_alerts':
                import gmgn_monitor as gmgn
                gmgn.schema(con)
                canonical={r[0] for r in con.execute('SELECT alert_id FROM gmgn_daily')}
            columns = ('id,time,symbol,direction,change,base_price,price,base_time,event_time,basis,acknowledged'
                       if table == 'spike_alerts' else 'id,chain,address,rule,triggered,last_seen,acknowledged,data')
            def decode(row):
                value = dict(row)
                if 'data' in value:
                    value['token'] = token(json.loads(value.pop('data')))
                return value
            new = con.execute(f'SELECT {columns} FROM {table} WHERE id>? ORDER BY id LIMIT ?',
                              (int(cursor.get(table, 0)), EVENT_LIMIT + 1)).fetchall()
            has_more = len(new) > EVENT_LIMIT
            new = new[:EVENT_LIMIT]
            recent = con.execute(f'SELECT {columns} FROM {table} ORDER BY id DESC LIMIT 100').fetchall()
            decoded_new = [decode(r) for r in new]
            decoded_recent = [decode(r) for r in recent]
            suppressed = []
            if table == 'gmgn_alerts':
                from gmgn_equities import equity_reason
                hidden = {r['id']:r for r in decoded_new+decoded_recent if equity_reason(r.get('token') or {}) or r['id'] not in canonical}
                suppressed = [dict(id=r['id'],chain=r['chain'],address=r['address'],reason='tokenized_equity' if equity_reason(r.get('token') or {}) else 'duplicate_beijing_day')
                              for r in hidden.values()]
                decoded_new = [r for r in decoded_new if r['id'] not in hidden]
                decoded_recent = [r for r in decoded_recent if r['id'] not in hidden]
            result[table] = dict(new=decoded_new, recent=decoded_recent, has_more=has_more, suppressed=suppressed)
            if new:
                next_cursor[table] = new[-1]['id']
            more |= has_more
    return result, next_cursor, more


def build_packet(collector_id, sequence=0, cursor=None, now=None):
    now = time.time() if now is None else now
    cursor = {} if cursor is None else cursor
    context = dict(now=now)
    modules = {}
    for name, (version, reader) in SECTIONS.items():
        try:
            modules[name] = dict(version=version, status='ok', data=reader(context))
        except Exception:
            # Other modules and the packet continue; no fake empty-success snapshot.
            modules[name] = dict(version=version, status='unavailable', data=None)
    events, next_cursor, more = read_events(cursor)
    packet = dict(schema='perp-workbench.monitor', schema_version=SCHEMA_VERSION,
                  collector_id=collector_id, packet_id=str(uuid.uuid4()), sequence=sequence,
                  generated_at=now, timezone='Asia/Shanghai',
                  kind='snapshot_with_alert_events', modules=modules, events=events,
                  event_cursor=dict(before=cursor, after=next_cursor, has_more=more),
                  coverage=dict(raw_database_included=False, credentials_included=False,
                                history='Available retained alert records, then incremental new alerts; latest 100 states repeated.',
                                candles='Raw candle/depth/tick history is not included; current prices, 4h baselines and OI metrics are included.'))
    return finite(packet)


def encode(packet):
    return json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
