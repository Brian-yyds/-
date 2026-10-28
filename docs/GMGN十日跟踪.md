# GMGN每日去重与10日报警代币榜

## 警报

按 `(链,合约地址,北京时间自然日)` 最多生成一条报警，两个规则分支共用上限。EVM地址不区分大小写，Solana地址区分大小写；不同链即使地址相同也分别处理。确认或重启不重置上限。历史旧记录不删除，但近3条展示及同步有效事件按同币同日合并，旧重复ID可通过`suppressed`标记停用。

首次报警：今天毕业的发射台代币，5分钟美元成交额严格大于150K、KOL大于1（至少2人）；非发射台代币5分钟美元成交额严格大于1M、KOL大于1，无今日限制。两类不要求聪明钱人数或合计人数，缺少聪明钱数据不会拦截满足条件的代币；缺少KOL人数不报警。币股/ETF继续排除。

第一次真实报警后进入10×24小时跟踪。已报警的毕业代币在这10天内复报时，沿用150K、毕业状态及KOL门槛，不再要求“今日毕业”。复报不要求超过上次报警的成交额，而是再次突破当前配置门槛。

持续高于成交额门槛不会每天机械重报。需要有有效观测证明5分钟成交额连续至少5分钟回落至门槛以内，再次突破且人数符合才可复报，并且当天尚未报警。观测间隔超过120秒的空白不累计为连续回落；缺失数据和名单消失不视为回落，KOL人数变化也不重置成交额状态。

首次报警时间、每日已报日期和成交额去重状态持久保存。旧警报自动迁移为首次报警起点，重复历史不延长窗口。10天到期后不再跟踪或复报同一轮，也不会用后续报警把同一CA重新推回榜内。历史记录仍保留供核对。

## 排行

原GMGN热搜及成交榜首已移除，也不再请求热搜或带50天/30M筛选的榜首数据。左侧改为10日报警代币榜，右侧保留最近3条报警，直接显示原始触发的北京时间，完整日期及秒（YYYY-MM-DD HH:mm:ss）。使用不可变的`triggered`，而不是后续行情更新的`last_seen`，每条HTML时间同时保留+08:00时区。

入榜范围只包含真实报警过、尚未超过首次报警后10天、且不是币股的代币。排名使用官方`token info`返回的`price.volume_1h`（滚动近1小时美元成交额），按成交额降序取前10。零成交额是有效值；缺失、负数、非有限值或超过180秒未更新的记录不作为当前排名。不是代币创建日期，也不是把十天成交额相加。

离开热门候选名单仍按CA逐个查询已报警代币。每个代币详情最短60秒再次更新，每轮最多20个，按最久未尝试者轮换；每批最多10个，批内请求间隔至少1.1秒。总更新时延还包括接口耗时、全链候选扫描、15秒轮间等待、队列规模及限流。24个代币也可能分两轮补齐，不承诺所有代币每秒更新。

同一批内失败的代币保留旧数据，过期后退出有效排名；显示`小时数据 已更新/总数`时，前10仅代表当前有效数据的前10，不能当成全池已经完整排序。接口限流沿用全局冷却，不抢跑重复查询。

5分钟候选接口仍用于发现首次报警，覆盖SOL、BSC、Base、ROB每链成交额前100；不宣称遍历全链全部CA。一个链的接口失败不阻止其他链和已报警代币详情更新，遇到限频则停止本轮后续请求。

## 同步合同

每分钟SFTP同步保留；GMGN模块版本升为2：

- 删除 `hot/hot_leaders/hot_updated/volume_leader`，新增 `watch_ranking`。
- `watch_ranking.rows` 最多10项，包含`chain/address/token/volume_1h/volume_observed/first_alert/expires_at/last_alert`。
- `total/fresh/complete/fresh_max_age_seconds` 描述排名覆盖与新鲜度；每包重算有效期，不会把旧榜单再次发送为实时完整结果。
- `rules.graduate_volume=150000`、`kol_count=1`、`smart_money_required=false`、`alarm_time_timezone=Asia/Shanghai`；旧`people`人数门槛已移除。事件`triggered`继续使用Unix秒，接收端按北京时间显示。
- `rules.daily_limit=1`、`tracking_days=10`、`daily_timezone=Asia/Shanghai`、`tracking_anchor=first_alert`。
- `events.gmgn_alerts.suppressed` 可含`duplicate_beijing_day`及既有`tokenized_equity`，用于接收端停用先前接收的旧记录。游标按原始记录推进，不因过滤卡住。

SQLite表：`gmgn_watch`保存固定观察窗、小时值及详情刷新状态；`gmgn_daily`保存每日报警唯一键；`gmgn_episodes`保存每代币当前成交额状态。已有报警和同步游标保留。

字段来源：[GMGN官方 token info 说明](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md)，只调用官方SDK的只读`getTokenInfo`与5分钟候选接口。
