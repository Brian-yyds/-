import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import store
import jobs
import research
from launch import initialize, CONFIG

st.set_page_config(page_title="Perp Desk · 永续研究工作台", page_icon="◈", layout="wide")
st.markdown("""<style>
.stApp {background:#0b1017;color:#e6ebf1}
[data-testid="stSidebar"] {background:#111923;border-right:1px solid #243040}
h1,h2,h3 {letter-spacing:-.035em}
[data-testid="stMetric"] {background:#121c28;border:1px solid #263346;border-radius:12px;padding:18px}
[data-testid="stMetricLabel"] {color:#9cacbf}
.eyebrow{color:#b4ed78;font:12px monospace;letter-spacing:.14em}
.note{color:#8e9eb0;font-size:13px;line-height:1.7}
div[data-testid="stTabs"] button {font-weight:600}
a {color:#b4ed78}
</style>""", unsafe_allow_html=True)
config = initialize()


def local_time(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")


def show_job():
    job = jobs.status()
    if not job:
        return
    labels = {"running": "运行中", "completed": "已完成", "failed": "失败"}
    st.info(f"最近任务 · {job['action']} · {labels[job['state']]} · {local_time(job['started_at'])}")
    path = store.USER / job["log"]
    if path.exists():
        with st.expander("查看任务日志", expanded=job["state"] == "failed"):
            st.code(path.read_text(encoding="utf-8", errors="replace")[-10000:], language="text")


with st.sidebar:
    st.markdown("### ◈ PERP DESK")
    st.caption("币安 USDT 永续 · 15m")
    st.divider()
    st.markdown("**研究引擎**  Freqtrade 2026.9\n\n**因子分析**  Alphalens Reloaded")
    st.link_button("打开 FreqUI 回测界面 ↗", "http://127.0.0.1:8080", use_container_width=True)
    with st.expander("FreqUI 本机登录信息"):
        st.code("research", language="text")
        st.text_input("本机随机密码", value=config["api_server"]["password"], type="password")
        st.caption("登录时 Bot URL 使用 http://127.0.0.1:8080。密码仅保存在本机配置文件。")
    st.divider()
    st.caption("公共行情只读 · 无交易所密钥\n\n后台采集随启动器运行；关闭浏览器不中断。电脑休眠、断网或停止启动器会中断采集。")
    st.markdown("[Freqtrade 源码](https://github.com/freqtrade/freqtrade) · [Alphalens 源码](https://github.com/stefan-jansen/alphalens-reloaded)")

st.markdown('<div class="eyebrow">RESEARCH BEFORE EXECUTION</div>', unsafe_allow_html=True)
st.title("永续合约研究工作台")
st.caption("从市场异动到因子检验，再到可复现的策略回测。行情时间为北京时间；研究数据与回测区间使用 UTC。")
market_tab, backtest_tab, factor_tab, data_tab, guide_tab = st.tabs(["市场监控", "策略回测", "因子研究", "数据与任务", "方案与边界"])


@st.fragment(run_every="30s")
def market_view():
    snapshot = store.get("market")
    health = store.get("collector", {})
    if health.get("errors"):
        st.warning("采集异常：" + "；".join(health["errors"][:4]))
    if not snapshot:
        st.info("尚无真实行情。后台正在尝试连接币安公共永续接口；连接失败时这里会保持空白，不会替换成演示价格。")
        return
    age = time.time() - snapshot["fetched_at"]
    if age > 90:
        st.error(f"数据已过期：距上次成功更新 {age / 60:.1f} 分钟。请检查采集进程和网络。")
    else:
        st.caption(f"● 最近成功更新 {local_time(snapshot['fetched_at'])} · 30秒刷新 · 来源 fapi.binance.com")
    frame = pd.DataFrame(snapshot["tickers"])
    a, b, c, d = st.columns(4)
    a.metric("在交易的 USDT 永续", len(frame))
    b.metric("上涨占比 · 24h", f"{(frame.change24h_pct > 0).mean():.1%}")
    c.metric("24h 总成交额 · USDT", f"{frame.quote_volume.sum()/1e9:,.2f} B")
    d.metric("自选交易对", len(store.settings()["watchlist"]))
    left, right = st.columns([2, 1])
    with left:
        query = st.text_input("搜索交易对", placeholder="BTC / ETH / SOL …", key="market_search")
    with right:
        only_watch = st.checkbox("仅看自选", value=False)
        sort = st.selectbox("排序", ["成交额", "24h涨幅", "24h跌幅", "资金费率绝对值"])
    shown = frame.copy()
    if query:
        shown = shown.loc[shown.symbol.str.contains(query.upper(), regex=False)]
    if only_watch:
        shown = shown.loc[shown.symbol.isin(store.settings()["watchlist"])]
    key = {"成交额": "quote_volume", "24h涨幅": "change24h_pct", "24h跌幅": "change24h_pct", "资金费率绝对值": "funding_bps"}[sort]
    shown = shown.sort_values(key, ascending=sort == "24h跌幅", key=lambda x: x.abs() if sort == "资金费率绝对值" else x)
    metrics = store.get("metrics", {})
    shown["15m涨跌 %"] = shown.symbol.map(lambda s: metrics.get(s, {}).get("move15m_pct") if time.time()*1000-metrics.get(s, {}).get("close_time",0) < 960000 else None)
    shown["量比 / 前20根"] = shown.symbol.map(lambda s: metrics.get(s, {}).get("volume_ratio") if time.time()*1000-metrics.get(s, {}).get("close_time",0) < 960000 else None)
    columns = {"symbol": "交易对", "price": "最新价", "change24h_pct": "24h涨跌 %", "quote_volume": "24h成交额 USDT", "funding_bps": "最新资金费率 bps", "mark_price": "标记价格"}
    st.dataframe(shown[list(columns) + ["15m涨跌 %", "量比 / 前20根"]].rename(columns=columns), hide_index=True, use_container_width=True,
                 column_config={"24h涨跌 %": st.column_config.NumberColumn(format="%.2f"), "最新价": st.column_config.NumberColumn(format="%.6f"), "最新资金费率 bps": st.column_config.NumberColumn(format="%.3f")})
    st.caption("15m 指标仅采集自选交易对，并且只使用已收盘 K 线。资金费率以每个合约实际结算周期为单位，未统一换算成年化。1 bps = 0.01%。")
    with st.expander("自选 K 线 · 最近25根"):
        selected = st.selectbox("交易对", store.settings()["watchlist"])
        rows = store.get("candles:" + selected, [])
        if rows:
            raw = pd.DataFrame(rows)
            closed = raw.loc[raw[6].astype("int64") < int(time.time()*1000)]
            fig = go.Figure(go.Candlestick(x=pd.to_datetime(closed[0], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai"),
                        open=closed[1].astype(float), high=closed[2].astype(float), low=closed[3].astype(float), close=closed[4].astype(float),
                        increasing_line_color="#b4ed78", decreasing_line_color="#f2788f"))
            fig.update_layout(template="plotly_dark", height=330, margin=dict(t=20,b=10), xaxis_rangeslider_visible=False, paper_bgcolor="#0b1017", plot_bgcolor="#0b1017")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("该交易对尚未采集到 K 线。")
    st.subheader("异动记录")
    rows = store.recent_alerts()
    if rows:
        alerts = pd.DataFrame(rows, columns=["time", "交易对", "类型", "数值", "说明"])
        alerts["time"] = alerts.time.map(local_time)
        st.dataframe(alerts.rename(columns={"time": "北京时间"}), hide_index=True, use_container_width=True)
    else:
        st.caption("暂无达到规则阈值的真实异动。")


with market_tab:
    market_view()
    with st.expander("编辑自选与本地异动规则"):
        cfg = store.settings()
        with st.form("rules"):
            watch = st.text_area("自选（最多30个，以英文逗号分隔）", ", ".join(cfg["watchlist"]))
            c1, c2, c3, c4 = st.columns(4)
            move = c1.number_input("15m涨跌绝对值 %", min_value=0.1, max_value=50.0, value=float(cfg["move15m_pct"]))
            volume = c2.number_input("成交量倍数", min_value=1.1, max_value=100.0, value=float(cfg["volume_ratio"]))
            funding = c3.number_input("资金费率绝对值 bps", min_value=0.1, max_value=1000.0, value=float(cfg["funding_abs_bps"]))
            cooldown = c4.number_input("重复提醒冷却 / 分钟", min_value=1, max_value=1440, value=int(cfg["cooldown_minutes"]))
            if st.form_submit_button("保存规则"):
                symbols = list(dict.fromkeys(s.strip().upper() for s in watch.split(",") if s.strip()))
                valid = set(store.get("exchange_info", {}).get("symbols", []))
                if not 1 <= len(symbols) <= 30 or any(not s.isalnum() or not s.endswith("USDT") for s in symbols):
                    st.error("请输入1~30个形如 BTCUSDT 的符号。")
                elif valid and set(symbols) - valid:
                    st.error("存在非在交易 USDT 永续：" + ", ".join(set(symbols)-valid))
                else:
                    store.put("settings", {"watchlist": symbols, "move15m_pct": move, "volume_ratio": volume, "funding_abs_bps": funding, "cooldown_minutes": cooldown})
                    st.success("已保存；采集器将在下一次轮询生效。异动只写入本地记录。")

with backtest_tab:
    st.subheader("使用成熟引擎，保留每一次实验")
    st.markdown("回测与结果图表由 **Freqtrade / FreqUI** 提供。已配置 15m 永续、多空信号、逐仓1倍、单笔500 USDT、最多3笔；这是验证流程用的均线基线。")
    st.link_button("进入 FreqUI · 数据下载 / 回测 / 图表 / 结果比较 ↗", "http://127.0.0.1:8080")
    with st.form("backtest_job"):
        a, b, c = st.columns(3)
        start_date = a.date_input("开始日期 · UTC", datetime.now(timezone.utc).date()-timedelta(days=30))
        end_date = b.date_input("结束日期 · UTC，不含当天", datetime.now(timezone.utc).date())
        fee_bps = c.number_input("单边手续费 bps", min_value=0.0, max_value=100.0, value=5.0)
        detail = st.checkbox("使用已下载的5m细节数据检查15m内成交", value=False)
        task = st.selectbox("任务", ["回测", "未来数据检测", "递归指标检测"])
        if st.form_submit_button("提交到 Freqtrade", type="primary"):
            if start_date >= end_date:
                st.error("开始日期必须早于结束日期。")
            else:
                try:
                    jobs.start({"回测":"backtest", "未来数据检测":"lookahead", "递归指标检测":"recursive"}[task],
                               timerange=start_date.strftime("%Y%m%d")+"-"+end_date.strftime("%Y%m%d"), fee=fee_bps/10000, detail=detail)
                    st.success("任务已提交。在“数据与任务”查看日志，完成后可在 FreqUI 加载结果。")
                except Exception as exc:
                    st.error(str(exc))
    st.warning("成本口径：手续费按输入值每边收取；资金费依赖实际下载覆盖。原生回测未完整模拟盘口冲击和排队，不能把结果视作可成交收益。可将单边费率提高5/10bps做额外成本敏感性情景，但这只是成本压力近似。")
    st.caption("建议：先在前70%数据检验逻辑，再固定参数测试后30%；不要反复查看样本外结果后继续选参。基线收益未预先验证。")

with factor_tab:
    st.subheader("横截面因子研究")
    st.caption("使用本地 Freqtrade 15m 永续数据，Alphalens 计算 Rank IC、五组收益和换手；不重复实现这些统计引擎。")
    a,b = st.columns([2,1])
    factor_name = a.selectbox("因子", list(research.FACTORS))
    horizon = b.selectbox("未来收益窗口 / 根", [1,4,16], index=1, format_func=lambda n:f"{n} 根 · {n*15} 分钟")
    st.info(research.FACTORS[factor_name])
    st.caption("收盘后生成因子 → 下一根开盘进入 → 第 h+1 根开盘退出。固定 UTC 网格抽取不重叠标签；按共同区间70/30切分并剔除跨界标签。每个时点至少10个币、5个有效分组。")
    if st.button("运行 Alphalens 检验", type="primary"):
        try:
            with st.spinner("读取本地数据并分析…"):
                histories, problems = research.load_histories(config["exchange"]["pair_whitelist"])
                for problem in problems:
                    st.warning(problem)
                panel, label, cut = research.build_panel(histories, factor_name, horizon)
                results = {s: research.analyze_panel(panel, label, s) for s in ["样本内", "样本外"]}
                st.session_state["factor_result"] = (factor_name, horizon, panel, label, cut, results)
        except Exception as exc:
            st.error(f"未生成结果：{exc}")
    if "factor_result" in st.session_state:
        name,h,panel,label,cut,results = st.session_state["factor_result"]
        st.markdown(f"**最近实验：{name} · {h}根**　分割点 UTC：{cut}")
        oos = results["样本外"]
        a,b,c,d = st.columns(4)
        a.metric("样本外平均 Rank IC", f"{oos['mean_ic']:.4f}")
        b.metric("IC均值 / 标准差 · 未年化", f"{oos['ic_ir']:.3f}")
        c.metric("Q5 − Q1 · 毛收益", f"{oos['spread']:.3%}")
        d.metric("不重叠横截面数", oos["n"])
        fig = go.Figure()
        for segment, r in results.items():
            fig.add_trace(go.Scatter(x=r["ic"].index, y=r["ic"][label].rolling(20,min_periods=5).mean(), name=segment+" IC / 20期均值"))
        fig.update_layout(template="plotly_dark", height=300, paper_bgcolor="#0b1017", plot_bgcolor="#0b1017")
        st.plotly_chart(fig, use_container_width=True)
        means = pd.DataFrame({s:r["means"][label] for s,r in results.items()})
        st.bar_chart(means)
        st.dataframe(pd.DataFrame({s:{"平均IC":r["mean_ic"], "IC IR（未年化）":r["ic_ir"], "Q5-Q1毛收益":r["spread"], "横截面数":r["n"], "Q5单次换手":r["turnover"][5].mean()} for s,r in results.items()}).T)
        st.download_button("导出完整实验样本 CSV", panel.to_csv().encode("utf-8-sig"), file_name=f"factor-{h}bars.csv", mime="text/csv")
        st.download_button("导出IC和分组统计 JSON", json.dumps({"factor":name,"horizon_bars":h,"split_utc":str(cut),"results":{s:{"mean_ic":r["mean_ic"],"ic_ir":None if not np.isfinite(r["ic_ir"]) else r["ic_ir"],"gross_spread":r["spread"],"n":r["n"]} for s,r in results.items()}},ensure_ascii=False,indent=2),file_name="factor-summary.json")
        st.warning("分组收益是因子标签统计，未扣手续费、滑点和资金费，也不是组合净值回测。当前使用固定的现存交易对列表，存在幸存者偏差；币间相关性仍会影响有效样本。多次挖掘需要额外的滚动样本外验证。")

with data_tab:
    st.subheader("数据先可追溯，再做结论")
    st.markdown("历史行情、标记价格和资金费率由 Freqtrade 官方下载器管理；研究模块读取同一份数据。")
    with st.form("download_job"):
        days = st.selectbox("下载最近天数（含预热区间）", [7,30,90,180,365], index=2)
        detail = st.checkbox("同时下载5m数据（细节回测）", value=False)
        st.caption("固定研究池：" + " · ".join(p.split("/")[0] for p in config["exchange"]["pair_whitelist"]))
        if st.form_submit_button("下载 / 增量补齐历史数据", type="primary"):
            try:
                jobs.start("download", days=days, detail=detail)
                st.success("已提交官方下载器。任务会在浏览器关闭后继续，启动器应保持运行。")
            except Exception as exc:
                st.error(str(exc))
    if st.button("刷新任务与本地数据状态"):
        st.rerun()
    show_job()
    try:
        histories, problems = research.load_histories(config["exchange"]["pair_whitelist"])
        if histories:
            st.dataframe(research.dataset_summary(histories), hide_index=True, use_container_width=True)
        if problems:
            st.caption("；".join(problems))
    except Exception as exc:
        st.warning(f"数据引擎尚未就绪：{exc}")
    st.caption("网络限制、缺失资金费率、币种上市日期和数据缺口都可能限制可回测区间；不要用0资金费率掩盖缺失。")

with guide_tab:
    st.subheader("第一版范围与日常流程")
    st.markdown("""
1. **监控**：全市场在交易的 USDT 永续；自选池采集已收盘15m涨跌、成交量异常和最新资金费率；异动存本机 SQLite。
2. **回测**：Freqtrade / FreqUI 承担下载、策略执行模拟、结果图表与比较。均线基线只用于验证流程。
3. **因子**：Alphalens 检验动量、反转、波动、量能、收盘位置；使用跨币分组和不重叠的未来收益标签。
4. **检验**：冻结参数后跑样本外，再运行未来数据与递归指标检测，比较提高成本后的结果。

**每天**：检查数据时间与采集健康 → 查看异动 → 补齐历史数据 → 记录因子实验 → 在 FreqUI 比较策略 → 保留失败结果。

**下一阶段**：按上市/下市时间重建历史币池；持仓量/多空比数据持续归档；滚动训练和实验追踪；服务器服务化与外部通知；最后才评估模拟交易。

**运行边界**：当前为本机研究工作台。后台采集需要启动器、电脑和网络持续运行。本地规则不会发外部消息。没有接入实盘账户，也没有宣称策略有效。需要全天可靠运行时，应迁到可合法访问币安的常驻主机并配置进程守护。
""")
    st.markdown("[开源选型与来源](https://github.com/freqtrade/freqtrade) · [官方永续说明](https://www.freqtrade.io/en/stable/leverage/) · [官方回测假设](https://www.freqtrade.io/en/stable/backtesting/) · [币安永续接口](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)")
