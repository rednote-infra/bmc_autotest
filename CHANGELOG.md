# Changelog

本文件记录 `bmc_autotest` 项目的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 SemVer](https://semver.org/lang/zh-CN/)。

> 说明：**项目版本**与所依赖的 `redfish-python-sdk` **SDK 版本**相互独立。
> 每个项目版本均标注其锁定的 SDK 版本（含 commit），便于追踪脚本与 SDK 的兼容关系。

## [Unreleased]

## [1.0.1] — 2026-07-02

### Changed — SDK 升级至 v1.1.1
- 将 `redfish-python-sdk` 锁定升级至 **v1.1.1**（commit `2bddb5b`，2026-06-26）。
- 本次为纯增量小版本升级，所有旧调用方式完全向后兼容。完整变更见 `docs/redfish_python_sdk_api.md` 的「§8 版本变更记录（v1.1.1）」。
- 关键变更：
  - **日志路径发现重构**：由硬编码 `f"{log_services}/{log_id}/Entries"` 改为动态发现 `LogServices` 集合成员的真实 `@odata.id`，兼容非标准路径厂商。`log_id` 参数变为 Optional，单服务时自动选中。
  - **`subscribe()` 扩展**：新增 `http_headers`、`origin_resources`、`message_ids` 等 10+ 个 keyword-only 参数，原生支持 ZTE 所需的 HttpHeaders 字段。
  - **新增 `get_subscription()`**：按 ID 或完整 `@odata.id` 获取单个订阅。
  - **`delete_subscription()` 扩展**：参数支持传入完整 `@odata.id` 路径。
  - **`LogEntry` 新字段**：新增 `event_timestamp`、`diagnostic_data_size_bytes`。
  - **`Subscription` 模型收窄**：`http_headers` 和 `status` 字段类型放宽为 `Any`，兼容不同厂商格式差异。

### Changed — Event 脚本优化
- `event_003_create_subscription.py`：将 ZTE 降级代码（BmcHttpClient 手动 POST，约 40 行）替换为 SDK `subscribe(..., http_headers={...})`，大幅简化代码。
- `event_006_delete_subscription.py`：同上，简化前置 subscribe 的 ZTE 降级代码。
- `event_004_get_subscription.py`：改用新增的 `get_subscription()` 方法直接查询单个订阅，替代从集合取第一个的方式；移除过时注释"SDK 无 get_subscription"。

### Changed — Managers 日志脚本优化
- `managers_004a_sel_log_check.py`：消除硬编码 `entries_url`，改用 SDK `get_manager_log_entries()` 动态获取 Entries 集合，移除 [SDK-GAP] 标记。
- `managers_004b_operatelog_check.py`：同上。
- `managers_004c_auditlog_check.py`：同上。

### Docs
- `docs/redfish_python_sdk_api.md`：新增 §8 版本变更记录（v1.1.1），记录 `subscribe()` 扩展、`get_subscription()`、`LogEntry` 新字段、`Subscription` 模型变更等。
- `README.md`：更新 SDK 版本引用（v1.1.0 → v1.1.1）；更新 event 脚本的 SDK 接入度描述。

## [1.0.0] — 2026-06-23

### Changed — 脚本迁移至 SDK v1.1.0 类型化接口
将 10 个原用 `client.get_raw()` + `[SDK-GAP]` 占位的脚本改写为 v1.1.0 类型化接口
（计划见 `.codewiz/.plans/plan_sdk_v1.1.0_migration.md`）：
- `chassis_006a_uid_led_positive`：`set_indicator_led()` + `get_chassis().indicator_led`
- `chassis_007a_nvme_led_positive`：`set_drive_indicator_led()` + `get_drive()`（protocol/media_type/indicator_led 模型字段）
- `chassis_007b_nvme_led_negative`：读取改 `get_drive()`；**反向写非法值刻意保留 `client.patch()` 直达 BMC**，确保真正验证 BMC 服务端拒绝能力（避免 SDK 本地校验造成假 PASS）
- `chassis_011_nvme_power_test`：`get_drive()` + `drive_reset()`（`drive.actions` 按弱类型 dict 解析）
- `chassis_012_history_temp_test`：改用 `get_inlet_history_temperature()`；**测试目标由标准 ThermalSubsystem 调整为厂商扩展 InletHistoryTemperature 路径**（SDK 适配各厂商）
- `event_001_get_service`：`get_event_service()`（字段检查映射到模型属性）
- `event_009_submit_test_event`：`get_event_service()` + `submit_test_event()`
- `systems_003b_physical_drives_check`：`get_drive(odata_id)`（取代 get_raw + model_validate）
- `systems_004_boot_options_test`：新模型改用 `get_boot_options()` / `set_boot_option_enabled()`（旧 BootSourceOverride 模型保持不变）
- `systems_010a_sel_log_clear_positive`：Systems 侧 `get_system_log_service()` + `clear_system_log()`（Managers 侧 SDK 无封装，保留 get_raw + post）

### Docs
- `README.md`：更新上述脚本的 SDK 接入度列；从「SDK 已知局限」表移除 9 条已解决项（仅余 `systems_014` KVM）。
- `docs/redfish_python_sdk_api.md`：补充 v1.1.0 接口文档（§7 版本变更记录）。

### Notes
- ⚠️ 受 SDK 破坏性变更（`PowerSupply.line_input_voltage` int→float）影响的 `chassis_004_power_supplies_check` 经核查无需改动（未使用该字段，数值判断已兼容 float）。

### Added — 接口测试覆盖补充（v1.1.0 单资源 / 新接口）
- `systems_004_boot_options_test`：新模型切换后改用 `get_boot_option(id)` 独立 GET 回读验证（覆盖单资源接口，较依赖写接口返回值更严格）。
- `systems_007_sel_log_view`：新增单 LogService 资源验证，用 `get_system_log_service(id)` 探测 `#LogService.ClearLog` Action 可发现性（只读；Action 缺失记 WARNING，仅获取失败计 FAIL；Managers 侧跳过）。
- `managers_003_ethernet_interfaces_check`：由 `get_raw` 占位改用 `get_manager_ethernet_interfaces()` 类型化接口（保留数量/MAC/Enabled/IPv4/Speed/Status 六项检查）；**去除 `_` 前缀恢复执行**，加入 `bmc_runner_suites.json` managers 套件；原 EthernetInterface `NameServers=null` pydantic 解析 bug 随 SDK v1.1.0 修复（若仍解析失败则如实记 FAIL，不再静默绕过）。
- 接口覆盖核查：核对 `docs/redfish_python_sdk_api.md` 接口在脚本中的调用情况，补齐 `get_boot_option` / `get_system_log_service` / `get_manager_ethernet_interfaces` 覆盖。`get_host_interfaces` 经调研为 BMC↔主机带内管理通道（多数 BMC 不暴露），暂不纳入。

### Fixed
- 修正 `README.md` managers 表命名错位：实际 `managers_002` 为网络协议检查、`managers_003` 为以太网接口检查（原表头脚本名与功能错位）。

### Added — 诊断增强
- 新增 HTTP 请求 DEBUG 日志开关 `BMC_HTTP_DEBUG`（`func/bmc_diag.py`）：设为 `1` 时控制台实时打印每次接口调用的 URL 与请求 body（GET=DEBUG URL、POST/PATCH=INFO URL+payload、失败=ERROR 响应体），便于 FAIL 定位；默认关闭，请求细节始终写入 `log/bmc/<用例>/<用例>.log`。

## [1.0.0] — 2026-06-23

首个纳入版本管控的基线版本。

### 依赖
- 将 `redfish-python-sdk` 锁定至 **v1.1.0**（commit `3a6d841`）。
- SDK 由 v1.0.0 升级至 v1.1.0（纯增量），新增 `#LogService.ClearLog`、现代 BootOptions、
  `#Drive.Reset` / IndicatorLED 写、进风口历史温度、`#EventService.SubmitTestEvent` 等类型化接口，
  并引入 `models/check.py` 声明式校验引擎。完整变更见 `docs/redfish_python_sdk_api.md` 的「§7 版本变更记录」。

### 新增
- `requirements.txt`：以 commit hash 精确锁定 SDK 版本，确保可重复构建。
- `bmc/__init__.py`：定义项目版本号 `__version__` 与适配的 SDK 版本 `__sdk_version__`。
- 新增本 `CHANGELOG.md`。

### 注意
- ⚠️ **破坏性变更（SDK v1.0.x 起）**：`PowerSupply.line_input_voltage` 类型由 `int` → `float`（对齐 DMTF schema，BMC 可能返回 `220.5` 等小数）。
  使用该字段的脚本（如 `chassis_004_power_supplies_check.py`）需确认不存在整数类型假设。
