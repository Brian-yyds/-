"""Public USD-M endpoints only. No authenticated requests or order routes."""
import argparse
import json
import math
import time
from datetime import datetime, timezone
import requests
import store

BASE = "https://fapi.binance.com"
STEP = 900_000


class PublicClient:
    def __init__(self):
        self.session = requests.Session()
        self.blocked_until = 0

    def get(self, endpoint, **params):
        if time.time() < self.blocked_until:
            raise RuntimeError(f"接口退避中，{int(self.blocked_until - time.time())} 秒后重试")
        response = self.session.get(BASE + endpoint, params=params, timeout=(5, 15))
        if response.status_code in (418, 429):
            retry = response.headers.get("Retry-After", "")
            wait = int(retry) if retry.isdigit() else 300
            self.blocked_until = time.time() + max(wait, 60)
            raise RuntimeError(f"币安限流 HTTP {response.status_code}，暂停 {max(wait, 60)} 秒")
        if response.status_code == 451:
            self.blocked_until = time.time() + 900
            raise RuntimeError("币安返回 HTTP 451：当前网络/地区无法使用该接口；请在合规可访问的运行环境部署")
        response.raise_for_status()
        return response.json()


def closed_metrics(rows, now_ms):
    rows = [r for r in rows if int(r[6]) < now_ms]
    rows.sort(key=lambda r: int(r[0]))
    if len(rows) < 22:
        raise ValueError("已收盘 K 线不足 22 根")
    tail = rows[-22:]
    if any(int(b[0]) - int(a[0]) != STEP for a, b in zip(tail, tail[1:])):
        raise ValueError("15m K 线有缺口")
    if now_ms - int(tail[-1][6]) > STEP + 60_000:
        raise ValueError("15m K 线已过期")
    last = tail[-1]
    previous = tail[-2]
    mean_volume = sum(float(r[5]) for r in tail[-21:-1]) / 20
    return {
        "close_time": int(last[6]), "close": float(last[4]),
        "move15m_pct": (float(last[4]) / float(previous[4]) - 1) * 100,
        "volume_ratio": float(last[5]) / mean_volume if mean_volume > 0 else None,
    }


def poll(client):
    now = time.time()
    info = store.get("exchange_info")
    if not info or now - info["fetched_at"] > 3600:
        raw = client.get("/fapi/v1/exchangeInfo")
        info = {"fetched_at": now, "symbols": [s["symbol"] for s in raw["symbols"]
                if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
                and s["marginAsset"] == "USDT" and s["status"] == "TRADING"]}
        store.put("exchange_info", info)
    valid = set(info["symbols"])
    raw_tickers = client.get("/fapi/v1/ticker/24hr")
    premiums = {p["symbol"]: p for p in client.get("/fapi/v1/premiumIndex") if p["symbol"] in valid}
    tickers = []
    for t in raw_tickers:
        if t["symbol"] not in valid:
            continue
        p = premiums.get(t["symbol"], {})
        tickers.append({"symbol": t["symbol"], "price": float(t["lastPrice"]),
                        "change24h_pct": float(t["priceChangePercent"]),
                        "quote_volume": float(t["quoteVolume"]),
                        "funding_bps": float(p["lastFundingRate"]) * 10000 if p else None,
                        "mark_price": float(p["markPrice"]) if p else None,
                        "next_funding": p.get("nextFundingTime"), "exchange_time": t["closeTime"]})
    tickers.sort(key=lambda t: t["quote_volume"], reverse=True)
    store.put("market", {"fetched_at": time.time(), "tickers": tickers, "source": BASE})
    cfg = store.settings()
    metrics = store.get("metrics", {})
    errors = []
    for symbol in cfg["watchlist"][:30]:
        if symbol not in valid:
            errors.append(f"{symbol}: 不是在交易的 USDT 永续")
            continue
        try:
            old = metrics.get(symbol, {})
            boundary = int(now * 1000) // STEP * STEP
            if old.get("close_time", 0) < boundary - 1:
                rows = client.get("/fapi/v1/klines", symbol=symbol, interval="15m", limit=25)
                old = closed_metrics(rows, int(time.time() * 1000))
                metrics[symbol] = old
                store.put("candles:" + symbol, rows)
            t = next((t for t in tickers if t["symbol"] == symbol), None)
            cooldown = cfg["cooldown_minutes"] * 60
            # Never emit a signal from stale bars or a stale exchange snapshot.
            fresh_bars = time.time() * 1000 - old["close_time"] < STEP + 60_000
            if fresh_bars:
                for kind, threshold in [("move15m_pct", cfg["move15m_pct"]), ("volume_ratio", cfg["volume_ratio"])]:
                    value = old.get(kind)
                    active = value is not None and (abs(value) if kind == "move15m_pct" else value) >= threshold
                    if value is not None:
                        store.record_alert(symbol, kind, value, active,
                            f"{symbol} {kind}={value:.3f}；已收盘15m K线", cooldown)
            if t and t["funding_bps"] is not None and time.time() * 1000 - t["exchange_time"] < 120_000:
                value = t["funding_bps"]
                store.record_alert(symbol, "funding_bps", value, abs(value) >= cfg["funding_abs_bps"],
                                   f"{symbol} 最新资金费率 {value:.3f} bps（按该合约结算周期）", cooldown)
            time.sleep(0.12)
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    store.put("metrics", metrics)
    store.put("collector", {"heartbeat": time.time(), "ok": not errors, "errors": errors,
                            "pid_started": STARTED, "blocked_until": client.blocked_until})
    return len(tickers)


STARTED = time.time()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    client = PublicClient()
    while True:
        try:
            n = poll(client)
            print(json.dumps({"ok": True, "symbols": n, "time": datetime.now(timezone.utc).isoformat()}), flush=True)
        except Exception as exc:
            store.put("collector", {"heartbeat": time.time(), "ok": False, "errors": [str(exc)],
                                    "blocked_until": client.blocked_until})
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(max(30, min(900, client.blocked_until - time.time())))


if __name__ == "__main__":
    main()
