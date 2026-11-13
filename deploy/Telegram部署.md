# Telegram特级警报推送

`telegram_special.py`为服务器实际使用的发送进程源码。仅发送新增特级警报，包含代币名字、CA、GMGN链接和市值；使用独立SQLite发送记录去重、限频退避和重启恢复。首次接入会将已有警报作为基线，不回放历史。

## 安装目录

使用已有服务器监控安装的Python环境和`perp-monitor`用户。将本目录的`telegram_special.py`放到`/opt/perp-workbench/telegram/telegram_special.py`；创建仅监控用户可写的`/opt/perp-workbench/shared/telegram`目录。主监控数据库位于`/opt/perp-workbench/shared/user_data/workbench.sqlite`。

将`telegram.example`复制到服务器`/etc/perp-workbench/telegram.env`，仅在服务器内填写机器人Token及已核实的私人聊天ID；配置归root所有、权限600。用户需先打开自己的机器人聊天并发送/start。发送进程不提供聊天回复，也不通过公开消息自动更改接收人。若另外实现绑定程序，必须核实私人聊天、发送者和一次性指令；辅助函数binding_chats的bind_command应由配置显式提供，没有预设绑定口令。

将`perp-special-telegram.service.example`安装为`/etc/systemd/system/perp-special-telegram.service`，确认目录、账户与配置后重新加载服务定义，并启用该服务。此服务可单独更新，监控版本切换不会自动覆盖正在运行的Telegram脚本；更新前备份发送库并确认没有发送中的记录。

运行记录保存在`shared/telegram/delivery.sqlite`；不得上传GitHub。`sent`为Telegram已返回成功，`pending`为待发送或重试，`failed`为确定失败，`uncertain`为中断后无法确定是否已发送。uncertain不盲目重发，防止重复消息。历史迁移补录可标记为historical_backfill，保留记录但不作为新警报发送。

相关隔离测试见tests/test_telegram_special.py。测试使用内存数据库及模拟发送函数，不发真实消息。
