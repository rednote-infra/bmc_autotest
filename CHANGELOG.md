# Changelog

本文件记录 `bmc_autotest` 项目的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 SemVer](https://semver.org/lang/zh-CN/)。

> 说明：**项目版本**与所依赖的 `redfish-python-sdk` **SDK 版本**相互独立。
> 每个项目版本均标注其锁定的 SDK 版本，便于追踪脚本与 SDK 的兼容关系。

## [Unreleased]

## [1.3.2] — 2026-11-17

热修：修复真机验证发现的脚本崩溃、字段读取失效与判定标准偏差（issue/48，PR #49）。
共 15 个文件（+642/-342），新增 1 个公共模块。

### Fixed — 脚本崩溃

- `managers_023_sol_log_test`：`self.sol_log_path` 从未定义，SOL 采集成功后
  `open()` 抛 `AttributeError` 崩溃；崩溃致 `deactivate` 未执行（SOL 连接残留）、
  结果未落盘、退出码为 ERROR。补定义日志路径（`LOG_DIR/sol_capture.log`）；
  SOL 配置不可用时跳过连接与采集；采集全程 `try/finally` 保证进程回收与连接释放。

### Fixed — 字段读取恒为 None

- `managers_012_kvm_test`：`KvmService` 为 Pydantic 模型，PascalCase 仅为解析 alias，
  实例属性为 snake_case，故 `getattr(model, "PascalCase")` 读**已声明字段**恒返回 None
  （已声明字段不落入 `model_extra`）。连带 `original_timeout=None` 会在恢复时回写
  `None`，可能损坏 BMC 配置。改用 snake_case 属性访问；
  `TEST_SESSION_TIMEOUT` 60 → 30（原值超出 BMC 上限）。

### Fixed — 误判 FAIL（判定标准修正）

- `chassis_009_sensors_check`：未安装硬件的空槽位（`Status.State == "Absent"`，
  如空 NIC/NVMe/GPU 槽位）被划为"必要传感器"，空读值触发 ERROR → 大量误报 FAIL。
  改为空槽位按"未安装"跳过，不计入 PASS/FAIL；传感器组引入 **severity 分级**，
  硬盘温度、PCIe 卡温度异常降级 WARNING（CPU/内存/风扇/PSU/功耗仍为 ERROR）；
  PCIe 卡温度关键字去 `riser`、加 `slot`。
- `managers_026_default_config_check`：
  Sharelink 检查原依赖厂商 OEM 字段（无 NCSI 机型误报 FAIL）→ 改写为按网口角色判定；
  电源模式期望改为**均流**（非主备/热备），SDK 取不到 `Power.Redundancy` 时记 WARNING；
  错峰上电字段 SDK 未暴露时记 WARNING（待 SDK 适配）；IPv4 检查由"全部接口"改为
  **仅校验专用管理口**（避免内部口误判）。
- `managers_003_ethernet_interfaces_check`：原依赖厂商 OEM 扩展字段
  `Oem.Public.NCSIModeSelect` 判断网口角色，非所有厂商都提供该字段，导致 Sharelink 口
  被误判为专用口。改用统一约定定位（序号最小 = 专用口，其余 = Sharelink 口）。
- `account_004_role_test`：`Noaccess` 等"无访问权限"角色按设计 `AssignedPrivileges`
  即为空集合（空集为正确语义），被"权限非空"统一校验必然误判 FAIL。
  排除无权限角色出非空校验；只读角色等价名补充并改为大小写不敏感匹配，
  靠非标准名兜底时记 WARNING 提示，不掩盖标准名缺失。
- `ipmi_008_user_name_rule`：3 位用户名被列入"必须拦截"（未拦截 → FAIL），
  与 account_007 约定的"长度越界属宽松项，未拦截记 WARNING"冲突。
  移入宽松组，分类规范与 account_007 对齐。

### Fixed — 检查静默失效

- `protocol_001_dmtf_compliance`：403 权限检查所用测试账号的用户名不符合用户名规范，
  BMC 拒绝创建 → 该检查**长期从未真正执行**（降级 WARNING 掩盖）；且创建账号时
  未传 `Enabled`，BMC 默认禁用 → 登录返回 401（非预期的 403）。
  修复：测试账号用户名规范化、凭据收敛至 conf `TestAccount` 段、创建时显式 `Enabled=True`。
  另：空 JSON PATCH 判定由"必须 200/204"放宽为 **2xx/4xx 均可**
  （DSP0266 §9.6 用词为 `may accept`，服务拒绝属合法实现选择）。

### Changed — 能力调整

- `chassis_006a_uid_led_positive` / `chassis_006b_uid_led_negative`：移除 Blinking 态测试
  （运维确认不使用主动闪烁，无需推动厂商适配）；UID 指示灯测试由三态改为两态
  （Lit / Off）。

### Added

- `func/bmc_network_function.py`：管理网口角色判定公共函数
  （`classify_eth_roles` / `find_detect_ifaces` / `find_sharelink_ifaces`），
  供 `managers_003` / `managers_026` 共用，避免脚本层级互相引用。

## [1.3.1] — 2026-10-28

非标准 RoleId 'User' 修正 + SDK 1.3.0 接口覆盖扩展（issue/19，PR #20）。
共 17 个文件（+1499/-33），新增 4 个测试脚本与 4 个 conf。

### Fixed — 非标准 RoleId 'User' 修正（issue #19）

- 根因：DMTF DSP0266 标准 RoleId 枚举无 "User"（只读对应 ReadOnly/ReadOnlyUser），
  严格按标准实现的 BMC 拒绝创建 `RoleId="User"` 账号，导致 4 个 account 脚本 FAIL。
- `func/bmc_account_function.py`：新增 `resolve_role_id()` 角色归一化探测
  （ReadOnly → ReadOnlyUser → User 回落；角色列表不可读时 WARNING + 兜底 preferred）。
- `account_003a`：ROLE_TRANSITIONS 的 User → ReadOnly（高↔低权限边界语义不变），
  运行时经 `resolve_role_id()` 归一化。
- `account_004`：STANDARD_ROLES 改标准枚举；判定分层——Administrator/Operator
  缺失 FAIL；只读角色等价名（ReadOnly/ReadOnlyUser/User）任一存在即覆盖，
  全部缺失 WARNING 不 FAIL（宽松实现厂商避免误报）。
- `account_006/007`：创建账号 RoleId 改为运行时归一化探测。
- `account_test.json`：AltRoleId "User" → "ReadOnly"（14 处）。

### Added — SDK 1.3.0 接口覆盖扩展（A/B 档）

- `systems_015_secure_boot_test`：SecureBoot 三接口（查询存档 / SecureBootEnable
  切换读回 / ResetKeys Action 探测）；ResetKeys 默认 probe 不执行（破坏性操作），
  conf 可配 execute 实际执行；未广告 SecureBoot 链接 WARNING 跳过。
- `systems_016_memory_metrics_test`：MemoryDomains/Chunks/Metrics 逐 DIMM 读取，
  AlarmTrips 触发 FAIL；可选组件未广告 WARNING 跳过。
- `systems_017_telemetry_test`：Telemetry 11 接口全覆盖 + 单资源抽验（Id 回读一致）；
  TelemetryService 未广告或集合为空 WARNING 不 FAIL。
- `systems_018_network_interfaces_test`：带内网卡 + 端口 LinkStatus 分布存档
  （Down 不判 FAIL，网线未接属正常部署状态）；单端口抽验回读一致。
- `bmc_runner_suites.json`：systems 组新增 015/016/017/018 四个脚本。
- `docs/redfish_python_sdk_api.md`：1.1.1 → 1.3.0 全量（684 → 931 行），
  新增 §2.13~2.16（Systems 扩展/Telemetry/Composition/固件升级增强）与 §9 版本变更记录。
- README：Systems 表补 4 行；systems_014 接入度更正为 `get_kvm_service()`
  （SDK 1.3.0 已封装，消除最后一个 [SDK-GAP]）。

## [1.3.0] — 2026-10-26

真机验证反馈批量修复 + 上游 PR 可用点吸收（issue/9）。
基于多厂商真机验证（通算/存储/GPU 机型）反馈的 9 项脚本缺陷修复，
同时吸收上游多个未合入 PR 中经真机验证的有效改动。
共 58 个文件（+2241/-766），新增 3 个公共模块。

### Fixed — 真机验证反馈修复（9 项）

- `managers_003_ethernet_interfaces_check`：网口校验区分 Detect/Sharelink 口
  （`Oem.Public.NCSIModeSelect` 存在即为 Sharelink 口），Sharelink 口仅做 MAC
  格式校验，不再因统一要求 `InterfaceEnabled==True` 误判 FAIL。
- `managers_006b_port_enable_disable` / `managers_002_network_protocol_check`：
  移除 HTTP 检查项（业务侧明确不使用），仅保留 HTTPS。
- `managers_006_port_status_check`：KVM/IP 端口无 DMTF/IANA 标准，
  `==7578` 精确匹配改为「存在且合法（1-65535）」；HTTPS/SSH/IPMI 保留标准端口精确匹配。
- `systems_005_power_control_test` / `ipmi_001_power_status`：电源操作三级阈值
  持续轮询判定（≤120s PASS / 120~900s PASS+WARNING / >900s FAIL），
  仅真 FAIL 才触发 ForceOff 复位，避免残留状态污染下一步；
  ipmi_001 各操作独立阈值。
- `ipmi_010_user_delete`：删除判定改用 `priv=NO ACCESS`（IPMI Msg 列与 disable
  是协议语义上两个独立开关，原判定依据选错）；新增 `pre_safety_check` 前置步骤，
  目标槽位已有非预期用户名时中止测试，避免误删业务用户。
- `ipmi_013_user_session`：ipmitool 非零返回码为已知共性问题（各方认可），
  去掉 `rc != 0` 判定，仅以输出为空判失败。
- 用户名/密码规则全量整改（account + ipmi）：用户名 4~16 位全英文字母、
  密码 8~12 位四要素（大小写+数字+特殊字符，硬规定）；
  修正 conf 与脚本中所有违规用户名（含数字/下划线/长度不足）与超长密码。

### Changed — 测试用例重构

- `account_007_username_rule_test`：重写——正向 3×3 等价类×边界值矩阵（7 例）
  + user_name 原样保留校验（防截断/大小写规范化）+ 反向用例
  （长度越界→WARNING 不 FAIL，OEM 通用版本不强制定制；字符集违规→FAIL）
  + 逐用例「创建→验证→立即删除」防账号数上限误判。
- `account_003b/003c_password_modify_test`：正向 6 例（长度点×要素组合）
  + 旧密码失效双向校验（防假成功）；反向缺要素等价类 2→5 例
  + 长度越界宽松组 + 真实登录验证（防静默忽略假拦截）。
- `ipmi_012_user_disable`：废弃——唯一判定依据（IPMI Msg==false）不成立，
  业务认可现状；从 runner suite 移除，脚本保留废弃标注。

### Added — 机型差异适配与公共模块

- `bmc_runner`：新增 `--skip-no-gpu` / `--skip-no-storage` 机型差异跳过参数，
  跳过用例计入报告（`result='SKIPPED'`），三种报告格式同步展示；
  打通 exit_code=3 → SKIPPED 映射。
- 新增 `func/sel_log_helper.py`：SEL LogService 跨 Systems/Managers 统一定位
  （严格 `\bSEL\b` 独立词匹配 + 排除并列 EventLog 服务 + LogId 显式配置），
  6 个 SEL 脚本委托公共助手消除逻辑漂移；managers_004a 保留 OR 判定语义。
- 新增 `func/redfish_event_compat.py`：Event 测试公共辅助
  （订阅 EventTypes 交集决策 / SubmitTestEvent legacy-modern 模式探测 /
  Base Registry MessageId 解析）。
- `common_function`：新增跨平台工具 `is_windows()` / `ping_cmd()` / `ping_once()` /
  `ipmitool_bin()`（Windows 与 Linux 的 ping 参数、ipmitool 可执行名差异统一收敛）。
- `systems_005_power_control_test`：新增 ResetType UNSUPPORTED 第三态
  （AllowableValues 预检 + POST 400 收紧判定，机型差异不影响整体结果）；
  GracefulShutdown 超时 FAIL 后追加 OS 可达性诊断（仅诊断不改判定）。
- `systems_007_sel_log_view`：新增 `EmptyLogPolicy`（FAIL/SKIP）配置，
  条目为空默认 FAIL（防误选空服务假 PASS）。
- `update_002/003`：新增 `--vendor` 显式指定 / VendorDetector 自动探测
  （失败终止）；conf 厂商分段结构（`vendors.{厂商}` + `default` 回退）；
  BIOS 下电升级前置开关（`PowerOffBeforeUpgrade`）；SDK 连接重试参数全覆盖。

### Changed — 上游 PR 可用点吸收（均未合入 upstream/main，手动移植避免冲突）

- `web_012_one_click_log` / `systems_011_log_download_test`：一键日志改用 SDK
  `collect_and_download_diagnostic_data()` 一站式采集+下载+文件非空校验，
  消除「触发成功即 PASS」误判；systems_011 补传 `output_path`（证据留存）、
  `DiagnosticDataType` 改 None（SDK 厂商自适应）。
- `systems_010a_sel_log_clear_positive`：Managers 侧清除改用 SDK `clear_manager_log()`。
- `event_004_get_subscription`：空订阅误 FAIL → 自动创建临时订阅
  （唯一 Context+uuid 归属）→ 查询 → 精确清理。
- `event_003/006_create/delete_subscription`：订阅 EventTypes 硬编码
  → 与 `EventTypesForSubscription` 求交集，空则 None（厂商默认）。
- `event_009_submit_test_event`：双路径提交（ActionInfo 探测 modern=MessageId
  自动从 Base Registry 解析 / legacy=EventType 循环）+ 响应 Message 结构校验
  （字段缺失 WARNING 不 FAIL）；非标准私有参数不做兼容。
- `update_002/003`：`simple_update()` 增加 vendor 路由与厂商私有参数透传
  （Targets/镜像源凭据/ImageType/PreserveConf 经 kwargs 交 SDK 策略消费，
  脚本层保持厂商无关）。

### Changed — 配套 conf 更新

- `bmc_runner_suites.json`：managers 补全 17 个脚本（11→28，与目录对齐）；
  ipmi 移除 ipmi_012；systems_005 超时 5400→8000s、ipmi_001 新增 1000s。
- systems_005/007、ipmi_001、event、update、web_012、account 各 conf
  同步新增配置项（均带注释说明）。

### 依赖
- 将 `redfish-python-sdk` 锁定至 **v1.3.0**（PyPI 最新发布版本，2026-10-12）。
- SDK 演进路径（增量）：
  - **v1.3.0**（2026-10-12）：新增 Manager 服务类接口（NTP/Syslog/SNMP/LLDP/DNS/VNC/
    KVM/Security/VirtualMedia/SOL）、Manager 重置与日志清理（`reset_manager()` /
    `clear_manager_log()`）、SecureBoot/NetworkInterface/MemoryDomain/MemoryMetrics、
    固件上传与 BMC 更新（`simple_update()` vendor 路由 + `VendorDetector`）、
    Telemetry/Composition 全套、`collect_and_download_diagnostic_data()` 一键日志
    采集下载、`UpdateStrategyRegistry` 厂商更新策略框架。
  - 完整接口变更见 [`docs/redfish_python_sdk_api.md`](docs/redfish_python_sdk_api.md)。

### 验证

- 全部改动文件 `py_compile` + JSON 校验通过；
- 关键逻辑 mock 自测：sel_log_helper 12 例、redfish_event_compat 14 例、
  systems_005 UNSUPPORTED 判定 8 例、update 参数优先级 6 例、
  用户名/密码规则静态校验全量——全部通过；
- 三项规范复查通过：无新增硬编码 URL、无绕过 SDK 的调用、无厂商名暴露
  （延续去厂商化惯例）。

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
