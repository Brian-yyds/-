"""Local launcher. Only research commands are exposed; no live-trading action."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from store import ROOT, USER

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CONFIG = USER / "config.local.json"


def initialize():
    USER.mkdir(exist_ok=True)
    if not CONFIG.exists():
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        for key in ("password", "jwt_secret_key", "ws_token"):
            config["api_server"][key] = secrets.token_urlsafe(32)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        tmp.replace(CONFIG)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    changed = False
    # Upgrade the original disabled-Telegram stub without changing user settings.
    if not config.get("telegram", {}).get("enabled") and any(k not in config.get("telegram", {}) for k in ("token", "chat_id")):
        config.setdefault("telegram", {}).setdefault("token", "")
        config["telegram"].setdefault("chat_id", "")
        changed = True
    for key in ("ccxt_config", "ccxt_async_config"):
        options = config["exchange"].setdefault(key, {}).setdefault("options", {})
        if "fetchMarkets" not in options:
            options["fetchMarkets"] = {"types": ["linear"]}
            changed = True
    if not config["exchange"]["ccxt_async_config"].get("aiohttp_trust_env"):
        config["exchange"]["ccxt_async_config"]["aiohttp_trust_env"] = True
        changed = True
    if changed:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        tmp.replace(CONFIG)
    if config.get("dry_run") is not True or any(config["exchange"].get(k) for k in ("key", "api_key", "apiKey", "secret")):
        raise ValueError("此研究启动器要求 dry_run=true 且交易所密钥为空")
    if config.get("trading_mode") != "futures" or config.get("timeframe") != "15m":
        raise ValueError("本工作台限定15m永续研究配置")
    return config


def ft_args(command, *extra):
    return [sys.executable, "-m", "freqtrade", command, "--config", str(CONFIG),
            "--userdir", str(USER), *map(str, extra)]


def run_ft(command, *extra):
    # Freqtrade 2026.9 can return code 0 on ConfigurationError. Preserve log
    # evidence and fail closed for known fatal diagnostics rather than reporting success.
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    failed = False
    with subprocess.Popen(ft_args(command, *extra), cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env) as proc:
        for line in proc.stdout:
            print(line, end="", flush=True)
            if any(marker in line for marker in ("Configuration error:", "Fatal exception!", "No data found. Terminating.", "Traceback (most recent call last)")):
                failed = True
        code = proc.wait()
    if code or failed:
        raise SystemExit(code or 2)


def start(no_browser=False):
    import requests
    for url in ("http://127.0.0.1:8501/_stcore/health",):
        try:
            if requests.get(url, timeout=1).ok:
                raise RuntimeError("8501已被使用；请先停止已有服务，避免重复启动")
        except requests.exceptions.RequestException:
            pass
    children = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        cmds = {
            "collector": [sys.executable, str(ROOT / "live_collector.py")],
            "gmgn": [sys.executable, str(ROOT / "gmgn_monitor.py")],
            "volume": [sys.executable, str(ROOT / "volume_monitor.py")],
            "export": [sys.executable, str(ROOT / "export_worker.py")],
            "workbench": [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                          "--server.address", "127.0.0.1", "--server.port", "8501",
                          "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        }
        for name, cmd in cmds.items():
            log = (USER / f"{name}.log").open("a", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            child = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=env)
            log.close()
            children.append(child)
        (USER / "runtime.json").write_text(json.dumps({"pids": [p.pid for p in children]}), encoding="utf-8")
        print("永续异动监控 http://127.0.0.1:8501", flush=True)
        print("关闭浏览器后采集仍运行；按 Ctrl+C 停止全部服务。电脑需要保持运行和联网。", flush=True)
        for _ in range(60):
            if any(c.poll() is not None for c in children):
                raise RuntimeError("服务提前退出，请查看 user_data 中对应日志")
            try:
                if requests.get("http://127.0.0.1:8501/_stcore/health", timeout=1).ok:
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("工作台启动超时，请查看日志")
        if not no_browser:
            webbrowser.open("http://127.0.0.1:8501")
        while True:
            if any(c.poll() is not None for c in children):
                record = USER / "runtime.json"
                if record.exists() and json.loads(record.read_text(encoding="utf-8")).get("stopping"):
                    return
                raise RuntimeError("服务退出，请检查日志")
            time.sleep(2)
    except KeyboardInterrupt:
        print("正在停止服务…", flush=True)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        (USER / "runtime.json").unlink(missing_ok=True)


def stop():
    import psutil
    runtime = USER / "runtime.json"
    if not runtime.exists():
        print("没有本工作台的运行记录。")
        return
    record = json.loads(runtime.read_text(encoding="utf-8"))
    record["stopping"] = True
    runtime.write_text(json.dumps(record),encoding="utf-8")
    marker = str(ROOT).replace("\\", "/").lower() + "/"
    owned = []
    for pid in record.get("pids", []):
        try:
            proc = psutil.Process(pid)
            if any(marker in arg.replace("\\", "/").lower() for arg in proc.cmdline()):
                owned.append(proc)
        except psutil.Error:
            continue
    for proc in owned:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(owned, timeout=5)
    print("已停止本工作台主服务。已提交的研究任务如仍在运行，会独立完成。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "stop", "install-ui", "download", "backtest", "lookahead", "recursive"])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--timerange", default="")
    parser.add_argument("--fee", type=float, default=0.0005)
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.action == "stop":
        stop()
        return
    if args.action == "start":
        start(args.no_browser)
        return
    initialize()
    if args.action == "init":
        print("配置已生成；FreqUI 用户名 research，密码位于 user_data/config.local.json 的 api_server.password。")
    elif args.action == "start":
        start(args.no_browser)
    elif args.action == "install-ui":
        subprocess.run([sys.executable, "-m", "freqtrade", "install-ui"], check=True)
    elif args.action == "download":
        if not 3 <= args.days <= 1095:
            raise ValueError("下载天数范围3~1095")
        # A few extra days provide warmup before a desired testing start date.
        tf = ["15m", "5m"] if args.detail else ["15m"]
        run_ft("download-data", "--days", args.days, "--timeframes", *tf)
    else:
        if not args.timerange:
            raise ValueError("研究命令必须显式提供 --timerange YYYYMMDD-YYYYMMDD，便于区分样本内外")
        if not 0 <= args.fee <= 0.02:
            raise ValueError("单边成本比例必须在0~0.02范围")
        cmd = {"backtest": "backtesting", "lookahead": "lookahead-analysis", "recursive": "recursive-analysis"}[args.action]
        extra = ["--strategy", "PerpBaseline15m", "--timerange", args.timerange]
        if args.action != "recursive":
            extra += ["--fee", args.fee]
        if args.action == "backtest":
            extra += ["--export", "trades", "--cache", "none"]
            if args.detail:
                extra += ["--timeframe-detail", "5m"]
        run_ft(cmd, *extra)


if __name__ == "__main__":
    main()
