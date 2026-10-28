# 开源来源与复用方式

## 当前监控版

当前默认启动包含 Streamlit 监控界面、币安公共行情采集器和独立GMGN热门监控。实时推送使用开源 websockets（BSD-3-Clause），HTTP请求使用Requests（Apache-2.0），图表使用Plotly（MIT）。不重写交易所数据协议，不调用交易或账户接口。

2026-10-28新增采集同步：SFTP 使用 Paramiko 5.1.0（LGPL-2.1-or-later，https://github.com/paramiko/paramiko ），ed25519 密钥生成使用 cryptography；复用 SSH/SFTP 协议和密码学库，不自行实现密钥算法。Windows 私钥通过既有 DPAPI 保险箱加密，仅本机用户可解密。主机身份采用固定公钥校验，不自动信任未知或变化的公钥。接口依据 https://docs.paramiko.org/en/stable/api/client.html 和 https://docs.paramiko.org/en/stable/api/sftp.html 。

2026-10-25新增GMGN：通过已安装的 gmgn-cli 1.6.9（MIT，https://github.com/GMGNAI/gmgn-skills ）复用官方 OpenApiClient、输出清理及限频处理，仅查询5m候选与已报警代币详情（2026-10-29起不再查询热搜）。gmgn_bridge.mjs 的代理初始化参考该包 dist/index.js（MIT）。不打包node_modules或技能副本，不调用swap/order，不加载全局私钥配置。规则与中文紧凑界面属于本项目适配层。

2026-10-17新增订单流与流动性监控、持续确认去重，以及AI协议适配。行情仍直接使用币安公开的aggTrade、K线、OI和depth；AI通过Requests调用用户配置的标准接口，不引入另一个研究或交易框架。Windows密钥保险箱直接调用系统DPAPI，不自行实现加密算法。

下列Freqtrade与Alphalens属于已保留的旧版研究工具；默认启动不再启动FreqUI，也不在监控界面提供因子研究入口。研究工具的依赖单独保存在 requirements.research.txt。

核查日期：2026-10-16。此集成采用 GPL-3.0-or-later（见 LICENSE）。第三方包保留各自许可证；通过依赖安装，不将第三方实现改头换面声称为自研。

| 项目 | 来源 | 版本/参考 | 许可 | 实际用途 |
|---|---|---|---|---|
| Freqtrade | https://github.com/freqtrade/freqtrade | 2026.9；上游提交 `1f394eaebc2f46a83d26971388628707802f8602` | GPL-3.0 | 原样安装：永续行情下载、策略回测、偏差检测、Web API |
| FreqUI | https://github.com/freqtrade/frequi | 由 Freqtrade install-ui 安装兼容发行版；实际版本写入验证记录 | GPL-3.0 | 原样安装：回测管理、结果比较、K线与交易图表 |
| Alphalens Reloaded | https://github.com/stefan-jansen/alphalens-reloaded | 0.4.6 | Apache-2.0 | 直接调用 IC、分位收益与分位换手函数 |
| Streamlit | https://github.com/streamlit/streamlit | 1.66.0 | Apache-2.0 | 表格、表单、本机研究入口 |
| Plotly.py | https://github.com/plotly/plotly.py | 由安装锁定 | MIT | K线与IC图表 |

新增部分仅为：币安公共接口到本机缓存的采集适配、自选异动规则、Freqtrade任务启动、15m数据到Alphalens的格式与时间对齐适配，以及中文入口。没有新增回测引擎，也没有重写Alphalens统计。

策略 `PerpBaseline15m` 依据 Freqtrade 官方 IStrategy 接口编写，指标通过 TA-Lib 与 technical 提供。作为可运行基线，不附带盈利承诺。

上游说明：
- https://www.freqtrade.io/en/stable/freq-ui/
- https://www.freqtrade.io/en/stable/backtesting/
- https://www.freqtrade.io/en/stable/leverage/
- https://www.freqtrade.io/en/stable/data-download/
- https://github.com/stefan-jansen/alphalens-reloaded/blob/main/README.md
- https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
