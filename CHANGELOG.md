# Changelog

本文件记录 `bmc_autotest` 项目的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 SemVer](https://semver.org/lang/zh-CN/)。

> 说明：**项目版本**与所依赖的 `redfish-python-sdk` **SDK 版本**相互独立。
> 每个项目版本均标注其锁定的 SDK 版本，便于追踪脚本与 SDK 的兼容关系。

## [Unreleased]

### Added — 多项目适配（单例资源 ID 按项目区分）
- 新增 `func/info_check_function.py` 的项目区分能力：
  - `load_project_name()`：读取 `conf/bmc/bmc_runner_suites.json` 顶层 `project` 字段（未配置时回退环境变量 `BMC_PROJECT`）；
  - `is_anqing_project()` / `get_singleton_id()`：安擎项目 → `Self`，其他项目 → `1`；
  - `resolve_uri()` / `resolve_uris()`：仅把 `/redfish/v1/{Systems|Managers|Chassis}/1` 集合下的**第一个**资源 ID 段
    归一化为项目对应值，更深层的同名字段（如 `/Systems/1/Processors/1`）保持不变。
- `conf/bmc/bmc_runner_suites.json` 新增顶层 `project` 字段（默认 `Enginetech`，另支持 `AnQing` / `安擎` 别名）作为项目开关；
  **配置文件中所有 URI 保持 `/1` 不变**，由代码在运行时归一化，避免静态配置与项目绑定。
- HTTP 网关层统一接入归一化：`BmcWebClient._request()`（全部 web 套件，含配置文件 URI）、
  `BmcHttpClient.request()`、`protocol_001_dmtf_compliance._request()`、`systems_011_log_download_test._try_post()`。
- 脚本内硬编码 URI 常量改用 `resolve_uri()` / `resolve_uris()` 包裹：
  `managers_006b/007/009/026`、`_managers_008/010/011/012/013/015/016/017/018/019/020/021/022/023/024/025`、
  `systems_009/010b/011/014`、`update_002/003`、`bmc_stress_001_redfish_api_stress`、`web_001`/`web_007`。
- 非安擎项目下 `resolve_uri()` 为空操作，运行时行为与改动前完全一致。
- 文档同步：`README.md` 新增「使用方法 → 多项目适配（单例资源 ID）」，`docs/redfish_python_sdk_api.md` 的
  原始 JSON 访问与 `RedfishResource` 章节补充单例 ID 说明。

### Changed — 依赖升级（redfish-python-sdk 1.2.0 → 1.3.0）
- **升级原因**：1.2.x 的 `ChassisManager.get()` / `ManagersManager.get()` 直接请求
  `/redfish/v1/Chassis/1` 与 `/redfish/v1/Managers/1` 且**无兜底**，安擎固件返回 404，
  是安擎项目跑不通的根因；1.3.0 为此提供集合兜底。
- **SDK 侧变化**：
  - `ChassisManager.get()` / `ManagersManager.get()`：单例 ID 默认值改为 `Optional`，
    404 后回退集合 `Members[0].@odata.id`（仅对默认值生效，显式传参不兜底）；
  - 移除 `_get_chassis_collection()` 中 Lenovo 的 `/redfish/v1/Chassis/1` 硬编码兜底；
  - 新增 `EnginetechUpdateStrategy`（强制要求显式 `targets`）与
    `EnginetechLogCollectStrategy`（日志服务挂在 `Systems/Self/LogServices/BlackBox`）。
- 脚本从不显式传递 `chassis_id` / `manager_id` / `system_id`，升级后自动受益，用例无需改动。
- 同步更新 `requirements.txt`（`>=1.3.0`）与 `bmc/__init__.py::__sdk_version__`（`1.3.0`）。
- **已知代价**：安擎下 Chassis / Managers 每次调用多 1 次 404 GET 与 1 次重复的 `/redfish/v1/` 请求，
  属预期行为，非故障。

## [1.0.0] — 2026-09-03

首个纳入 Git Tag 版本管控的基线版本。

### 依赖
- 将 `redfish-python-sdk` 锁定至 **v1.2.0**（PyPI 最新发布版本，2026-08-05）。
- SDK 演进路径：
  - **v1.0.0**（2026-07-21）：首个正式发布版本。
  - **v1.1.0**（2026-08-04）：新增 `#LogService.ClearLog`、现代 BootOptions、`#Drive.Reset` / IndicatorLED 写、
    进风口历史温度、`#EventService.SubmitTestEvent` 等类型化接口，并引入 `models/check.py` 声明式校验引擎；
    同批还新增 `subscribe()` 的 `http_headers` 等扩展参数、`get_subscription()`、日志路径动态发现等增强。
  - **v1.2.0**（2026-08-05）：完善 OEM 项目支持（新增 10 个 OEM Service 模型与 12 个对应 `Client` 方法，
    如 `get_ntp_service()` / `get_syslog_service()` / `get_snmp_service()` / `get_vnc_service()` 等）。
  - 完整接口变更见 [`docs/redfish_python_sdk_api.md`](docs/redfish_python_sdk_api.md)。

### Changed — 脚本迁移至 SDK 类型化接口
将原用 `client.get_raw()` + `[SDK-GAP]` 占位的脚本改写为类型化接口：
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
- `managers_003_ethernet_interfaces_check`：由 `get_raw` 占位改用 `get_manager_ethernet_interfaces()` 类型化接口（保留数量/MAC/Enabled/IPv4/Speed/Status 六项检查）；**去除 `_` 前缀恢复执行**，加入 `bmc_runner_suites.json` managers 套件；原 EthernetInterface `NameServers=null` pydantic 解析 bug 随 SDK v1.1.0 修复（若仍解析失败则如实记 FAIL，不再静默绕过）。

### Changed — Event 脚本优化
- `event_003_create_subscription.py`：将 ZTE 降级代码（BmcHttpClient 手动 POST，约 40 行）替换为 SDK `subscribe(..., http_headers={...})`，大幅简化代码。
- `event_006_delete_subscription.py`：同上，简化前置 subscribe 的 ZTE 降级代码。
- `event_004_get_subscription.py`：改用新增的 `get_subscription()` 方法直接查询单个订阅，替代从集合取第一个的方式；移除过时注释"SDK 无 get_subscription"。

### Changed — Managers 日志脚本优化
- `managers_004a_sel_log_check.py`：消除硬编码 `entries_url`，改用 SDK `get_manager_log_entries()` 动态获取 Entries 集合，移除 [SDK-GAP] 标记。
- `managers_004b_operatelog_check.py`：同上。
- `managers_004c_auditlog_check.py`：同上。

### Added — 接口测试覆盖补充
- `systems_004_boot_options_test`：新模型切换后改用 `get_boot_option(id)` 独立 GET 回读验证（覆盖单资源接口，较依赖写接口返回值更严格）。
- `systems_007_sel_log_view`：新增单 LogService 资源验证，用 `get_system_log_service(id)` 探测 `#LogService.ClearLog` Action 可发现性（只读；Action 缺失记 WARNING，仅获取失败计 FAIL；Managers 侧跳过）。
- 接口覆盖核查：核对 `docs/redfish_python_sdk_api.md` 接口在脚本中的调用情况，补齐 `get_boot_option` / `get_system_log_service` / `get_manager_ethernet_interfaces` 覆盖。`get_host_interfaces` 经调研为 BMC↔主机带内管理通道（多数 BMC 不暴露），暂不纳入。

### Added — 诊断增强
- 新增 HTTP 请求 DEBUG 日志开关 `BMC_HTTP_DEBUG`（`func/bmc_diag.py`）：设为 `1` 时控制台实时打印每次接口调用的 URL 与请求 body（GET=DEBUG URL、POST/PATCH=INFO URL+payload、失败=ERROR 响应体），便于 FAIL 定位；默认关闭，请求细节始终写入 `log/bmc/<用例>/<用例>.log`。

### Added — 版本管控
- `requirements.txt`：以版本号锁定 SDK 依赖，确保可重复构建。
- `bmc/__init__.py`：定义项目版本号 `__version__` 与适配的 SDK 版本 `__sdk_version__`。
- 新增本 `CHANGELOG.md`。

### Fixed
- 修正 `README.md` managers 表命名错位：实际 `managers_002` 为网络协议检查、`managers_003` 为以太网接口检查（原表头脚本名与功能错位）。

### Docs
- `docs/redfish_python_sdk_api.md`：补充 v1.1.0 / v1.1.1 接口文档（`subscribe()` 扩展、`get_subscription()`、`LogEntry` 新字段、`Subscription` 模型变更、声明式校验引擎等）。
- `README.md`：更新 SDK 版本引用为当前锁定的 v1.2.0；更新 event 脚本的 SDK 接入度描述；从「SDK 已知局限」表移除已解决项（仅余 `systems_014` KVM 等 OEM 扩展项）。

### Notes
- ⚠️ **破坏性变更提醒（SDK v1.0.x 起）**：`PowerSupply.line_input_voltage` 类型由 `int` → `float`（对齐 DMTF schema，BMC 可能返回 `220.5` 等小数）。经核查 `chassis_004_power_supplies_check` 未使用该字段、数值判断已兼容 float，无需改动；其余使用该字段的脚本需确认不存在整数类型假设。
