# GMGN 社交信息流接入核查

核查日期：2026-10-25（北京时间）。核查范围为 GMGN 官方公开技能、官方 OpenApiClient 当前源码和本机已安装的 gmgn-cli 1.6.9；没有猜测或调用未公开的网页私有接口，没有读取浏览器登录凭据。

## 结果

公开接口中已确认存在三类可做信息流的数据，但没有找到直接同步 GMGN 网页 X / Twitter 社交媒体瀑布流的开放接口。不能据此断言 GMGN 内部没有该接口；仅凭现有 OpenAPI Key，当前无法确认能同步原版推文瀑布流。

| 数据 | 官方能力 | 与推文瀑布流的区别 |
|---|---|---|
| GMGN 社区喊单 | `callout global`，`POST /v1/callout/global`；支持多链、分页、KOL和宣言筛选 | GMGN 内部发布的喊单文字、喊单价格和持仓，不是 X 全量或关注账号推文 |
| KOL / 聪明钱动态 | `track kol` / `track smartmoney` | 钱包链上买卖记录，不是社交媒体发帖 |
| 代币社交信息 | `token info`，社交链接、账号和部分统计 | 项目资料，不是持续更新的推文正文流 |

GMGN 官方 `gmgn-narrative` 技能也把社交证据定义为单独的数据源：需配置 `SOCIAL_SEARCH_PROVIDER` 与 `SOCIAL_SEARCH_API_KEY`，支持官方 X API 或带 X 检索能力的 Grok API。这说明其叙事分析能力并不等同于 GMGN OpenAPI 已开放网页推文瀑布流。

## 后续接入途径

1. 要显示 GMGN 社区喊单，可以通过官方 `callout global` 轮询和事件去重实现，后续需用当前账号Key验证具体返回及套餐限制；当前未新增持续采集。
2. 要显示指定 X 账号或代币的真实推文，可另接官方 X 数据源，并按授权、套餐及接口能力处理实时流或轮询。Grok 的检索能力适合分析，不应承诺与 GMGN 网页瀑布流完全同步或完整覆盖。
3. 若必须与 GMGN 网页社交媒体流一致，需要 GMGN 提供该功能的官方开放接口和授权说明后再确认接入。

## 官方依据

- [GMGN OpenApiClient 最新源码](https://github.com/GMGNAI/gmgn-skills/blob/main/src/client/OpenApiClient.ts)
- [GMGN callout 技能](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-callout/SKILL.md)
- [GMGN track 技能](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-track/SKILL.md)
- [GMGN narrative 外部数据接口说明](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-narrative/SKILL.md)
