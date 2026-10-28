# GMGN警报的币股排除

此排除仅用于GMGN警报生成、待确认/近期历史显示，以及同步包里的有效GMGN警报。不缩减SOL / BSC / Base / ROB四链候选采集，也不改变币安的股票、ETF和大宗合约采集。GMGN已替换为10日报警代币榜，股票代币不进入此跟踪池。

识别按以下证据进行，不凭TSLA、GME等简称判断，也不把ROB整条链视为股票：

1. **发行方合约地址**：Backpack官方证券清单中的Solana mint；Robinhood官方assets端点中支持链的证券合约部署。首版静态目录为2026-10-28取得的88个Backpack和203个Robinhood合约，随源码提供为 `gmgn_equity_registry.json`，防止首次断网时丢失识别能力。
2. **证券平台标记**：`xstocks`、`flap_stocks`、明确的Backpack/Robinhood证券平台标记。
3. **名称补充证据**：新合约尚未进入目录但名称有对应链的明确发行方后缀，或包含明确的xStock/Tokenized stock/equity/ETF标记。

后台每天读取两次公开目录请求，每个来源一次；失败保留已有地址并在1小时后重试。不另行调用GMGN详情接口，不增加GMGN请求频率。首次目录缓存为种子文件加本机数据库更新，识别旧记录时也使用相同逻辑。不能从没有地址匹配、发行方或证券标记的未知元数据中保证识别全部币股；新增发行方可按同一方式扩充目录及平台标记。

历史警报原记录不删除。已识别的股票警报从有效待确认及最近3条展示中剔除，查询会向后寻找非股票记录；确认按钮不会把隐藏的股票记录自动确认为已读。同步包 `new/recent` 采用相同过滤，并输出近期被排除记录的 `suppressed` 标记，远端可停用此前已经接收的相同ID警报。游标按原始记录推进，不会因排除项停住。

GMGN两类警报均要求KOL人数大于2；今日毕业分支仍要求北京时间当天毕业、聪明钱与KOL计数合计大于3，5m成交额大于200K美元；非发射台分支5m成交额大于1M美元。边界等于阈值不触发。过滤与改阈值不清空持续命中的去重状态。

来源：

- [Backpack官方证券及合约目录](https://wallet.backpack.exchange/tokenized-stocks/assets)
- [Robinhood Stock Token APIs](https://docs.robinhood.com/chain/stock-token-apis/)，公开资产端点为 `https://api.robinhood.com/rhj/assets`。
- [GMGN官方平台选项说明](https://github.com/GMGNAI/gmgn-skills/blob/main/docs/cli-usage.md)，包含xstocks和flap_stocks平台标记。
