# BMC 自动化测试项目 SDK v1.1.0 迁移与版本管控计划

> **制定日期**：2026-06-23  
> **项目**：bmc_autotest（Redfish 协议 BMC 全功能自动化测试）  
> **SDK 版本升级**：v1.0.0 → v1.1.0（2026-06-15）  
> **计划范围**：脚本改写 + 版本管控方案设计

---

## 目录

1. [任务一：脚本改写计划](#任务一脚本改写计划)
   - [概述](#概述)
   - [脚本改写详细计划](#脚本改写详细计划)
   - [破坏性变更评估](#破坏性变更评估)
   - [实施阶段与依赖](#实施阶段与依赖)
2. [任务二：版本管控方案](#任务二版本管控方案)
   - [现状勘察](#现状勘察)
   - [版本锁定策略](#版本锁定策略)
   - [项目版本管理](#项目版本管理)
   - [兼容性追踪机制](#兼容性追踪机制)

---

# 任务一：脚本改写计划

## 概述

本项目共有 **10 个脚本** 需从 `get_raw()` + `[SDK-GAP]` 临时占位改为 v1.1.0 类型化接口。此外需评估 **1 个脚本** 受破坏性变更影响。

**改写原则**：
- 遵循项目测开六大基本原则（一脚本对应一需求、场景化、原子化、可判定 FAIL/PASS/WARNING）
- 改写不改变原有测试场景与判定语义
- 新接口不被某些 BMC 支持时，评估是否保留 `get_raw()` fallback
- 同步更新 README.md 接入度列与移除 `[SDK-GAP]` 标记

---

## 脚本改写详细计划

### 第一阶段：Chassis 指示灯操作（3 个脚本，可并行）

#### 1.1 `bmc/chassis/chassis_006a_uid_led_positive.py`

**原 SDK-GAP**：`Chassis.IndicatorLED` 写操作

**v1.1.0 新接口**：`set_indicator_led(state, chassis_id="1")`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 92 行 `client.patch()` + 第 107 行 `client.get_raw()` 回读 |
| **替换前** | `client.patch(chassis_odata_id, {"IndicatorLED": target_state})` + `client.get_raw()` 获取 IndicatorLED |
| **替换后** | `client.set_indicator_led(target_state)` 直接设置并返回新值；若需回读验证，可再调 `client.get_chassis().indicator_led`（v1.1.0 新增字段） |
| **异常处理** | 非法值（非 `Lit`/`Blinking`/`Off`）抛 `RedfishValidationError`，需捕获并记为 FAIL |
| **兜底策略** | 若 BMC 不支持 IndicatorLED，`set_indicator_led()` 抛 `RedfishValidationError`；脚本应捕获并记为 FAIL（不保留 fallback，因为这是功能缺陷） |
| **判定逻辑** | 保留原有三态切换 + 回读验证逻辑，仅替换底层调用 |
| **README 更新** | 将 `get_chassis() + get_raw()⚠️ [SDK-GAP]` 改为 `set_indicator_led()` |

**改写步骤**：
1. 删除第 100-103 行 `[SDK-GAP]` WARNING 日志
2. 第 92 行改为：`self.client.set_indicator_led(target_state)`
3. 第 107 行改为：`raw = self.client.get_raw(chassis_odata_id)` 或 `actual = self.client.get_chassis().indicator_led`（后者更优雅）
4. 异常捕获：`RedfishValidationError` 作为 FAIL 处理
5. 更新 README.md 第 77 行

**风险与兜底**：
- 若某些 BMC 的 `set_indicator_led()` 返回值为 None（不返回新值），需改为 `get_chassis()` 回读
- 若 BMC 不支持 IndicatorLED，异常会在 `set_indicator_led()` 时抛出，脚本应捕获并记为 FAIL

---

#### 1.2 `bmc/chassis/chassis_007a_nvme_led_positive.py`

**原 SDK-GAP**：`Drive.IndicatorLED` 写操作

**v1.1.0 新接口**：`set_drive_indicator_led(drive_odata_id, state)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 80 行 `client.patch()` + 第 95 行 `client.get_raw()` 回读 |
| **替换前** | `client.patch(drive_odata_id, {"IndicatorLED": target_state})` + `client.get_raw()` 获取 IndicatorLED |
| **替换后** | `client.set_drive_indicator_led(drive_odata_id, target_state)` 直接设置；回读可用 `client.get_drive(drive_odata_id).indicator_led`（v1.1.0 新增字段） |
| **异常处理** | 非法值抛 `RedfishValidationError`；不支持时也抛 `RedfishValidationError`，脚本应捕获并记为 FAIL |
| **兜底策略** | 无 fallback，因为 NVMe SSD 必须支持 IndicatorLED（规范要求） |
| **判定逻辑** | 保留原有 NVMe SSD 筛选 + 两态切换 + 回读验证逻辑 |
| **README 更新** | 将 `get_drives() + get_raw()⚠️ [SDK-GAP]` 改为 `set_drive_indicator_led()` |

**改写步骤**：
1. 删除第 88-91 行 `[SDK-GAP]` WARNING 日志
2. 删除第 152-155 行 `[SDK-GAP]` WARNING 日志（获取 Protocol/MediaType/IndicatorLED）
3. 第 80 行改为：`self.client.set_drive_indicator_led(drive_odata_id, target_state)`
4. 第 95 行改为：`raw = self.client.get_raw(drive_odata_id)` 或 `actual = self.client.get_drive(drive_odata_id).indicator_led`
5. 第 157 行获取 Protocol/MediaType 改为：`drive = self.client.get_drive(drive_odata_id); protocol = drive.protocol; media_type = drive.media_type`（v1.1.0 新增字段）
6. 异常捕获：`RedfishValidationError` 作为 FAIL 处理
7. 更新 README.md 第 79-80 行

**风险与兜底**：
- 若 `get_drive()` 返回的 Drive 模型中 `protocol`/`media_type`/`indicator_led` 仍为 None，需保留 `get_raw()` 作为 fallback
- 建议在改写时同时验证 v1.1.0 SDK 中 Drive 模型是否真的包含这些字段

---

#### 1.3 `bmc/chassis/chassis_007b_nvme_led_negative.py`

**原 SDK-GAP**：`Drive.IndicatorLED` 写操作（反向测试）

**v1.1.0 新接口**：`set_drive_indicator_led(drive_odata_id, state)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 155 行 `client.patch()` + 第 110 行 `client.get_raw()` 获取 Protocol/MediaType/IndicatorLED |
| **替换前** | `client.patch(drive_odata_id, body)` 发送非法值；`client.get_raw()` 获取 Protocol/MediaType |
| **替换后** | `client.set_drive_indicator_led(drive_odata_id, state)` 发送非法值；`client.get_drive()` 获取 Protocol/MediaType |
| **异常处理** | 反向测试：非法值应抛 `RedfishValidationError`，脚本捕获并记为 PASS；若 BMC 接受则记为 FAIL |
| **兜底策略** | 若 `set_drive_indicator_led()` 对非法值不抛异常（BMC bug），脚本应检测并记为 FAIL |
| **判定逻辑** | 保留原有反向测试逻辑（验证 BMC 拒绝非法值） |
| **README 更新** | 将 `get_drives() + get_raw()⚠️ [SDK-GAP]` 改为 `set_drive_indicator_led()` |

**改写步骤**：
1. 删除第 105-108 行 `[SDK-GAP]` WARNING 日志
2. 第 110 行改为：`drive = self.client.get_drive(drive_odata_id); protocol = drive.protocol; media_type = drive.media_type`
3. 第 155 行改为：`self.client.set_drive_indicator_led(drive_odata_id, body["IndicatorLED"])`（注意：body 是 dict，需提取值）
4. 异常捕获逻辑保持不变（反向测试期望异常）
5. 更新 README.md 第 80 行

**风险与兜底**：
- 若 `set_drive_indicator_led()` 对非法值不抛异常，脚本需检测返回值并记为 FAIL
- 建议在改写时补充对 `set_drive_indicator_led()` 返回值的验证

---

### 第二阶段：Chassis 硬件操作（2 个脚本，可并行）

#### 2.1 `bmc/chassis/chassis_011_nvme_power_test.py`

**原 SDK-GAP**：`Drive.Actions` / `Drive.PowerState`

**v1.1.0 新接口**：`get_drive(odata_id)` + `drive_reset(drive_odata_id, reset_type)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 149 行 `client.get_raw()` 获取 Actions/PowerState/MediaType；第 78 行 `client.post()` 执行 Drive.Reset |
| **替换前** | `client.get_raw(drive_odata_id)` 获取原始 JSON；`client.post(reset_action_target, {"ResetType": reset_type})` 执行 Reset |
| **替换后** | `client.get_drive(drive_odata_id)` 获取 Drive 对象（含 `power_state`/`actions` 字段）；`client.drive_reset(drive_odata_id, reset_type)` 执行 Reset |
| **异常处理** | `drive_reset()` 若 BMC 不支持 #Drive.Reset，抛 `RedfishValidationError`；脚本应捕获并记为 WARNING（不 FAIL，因为这是厂商实现差异） |
| **兜底策略** | 若 `get_drive()` 返回的 Drive 模型中 `actions`/`power_state` 仍为 None，需保留 `get_raw()` 作为 fallback；若 `drive_reset()` 不支持，保留 `client.post()` fallback |
| **判定逻辑** | 保留原有必要字段检查 + Reset 操作 + PowerState 回读验证逻辑 |
| **README 更新** | 将 `get_drives() + get_raw()⚠️ [SDK-GAP]` 改为 `get_drive()` + `drive_reset()` |

**改写步骤**：
1. 删除第 144-147 行 `[SDK-GAP]` WARNING 日志
2. 第 149 行改为：`drive = self.client.get_drive(drive_odata_id)` 并从 drive 对象提取字段
3. 第 166 行改为：`reset_action = drive.actions.get("#Drive.Reset") if drive.actions else None`（需验证 actions 结构）
4. 第 78 行改为：`self.client.drive_reset(drive_odata_id, reset_type)`（替换 `client.post()`）
5. 异常捕获：`RedfishValidationError` 作为 WARNING 处理（厂商未实现）
6. 第 89 行回读 PowerState 改为：`drive = self.client.get_drive(drive_odata_id); power_state = drive.power_state`
7. 更新 README.md 第 82 行

**风险与兜底**：
- 若 `get_drive()` 返回的 Drive 模型中 `actions` 为 None 或结构不同，需保留 `get_raw()` 作为 fallback
- 若 `drive_reset()` 不支持，需保留 `client.post()` fallback
- 建议在改写时同时验证 v1.1.0 SDK 中 Drive.actions 的结构（是否为 dict 还是对象）

---

#### 2.2 `bmc/chassis/chassis_012_history_temp_test.py`

**原 SDK-GAP**：历史温度全量 `get_raw()`

**v1.1.0 新接口**：`get_inlet_history_temperature(chassis_id="1")`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 80-113 行 HTTP GET 逻辑（标准 ThermalSubsystem 路径） |
| **替换前** | `requests.get()` 直接访问 `/redfish/v1/Chassis/1/ThermalSubsystem` 并解析 JSON |
| **替换后** | `client.get_inlet_history_temperature()` 返回 `InletHistoryTemperature` 对象或 None |
| **异常处理** | 若 BMC 不支持或返回 404，`get_inlet_history_temperature()` 返回 None；脚本应检测并记为 FAIL（标准路径不通） |
| **兜底策略** | 标准路径失败时，脚本仍可探测 OEM 路径（`/redfish/v1/Chassis/1/Thermal/InletHistoryTemperature`）作为参考记录，但标准路径不通仍为 FAIL |
| **判定逻辑** | 保留原有历史数据完整性验证逻辑（非空、字段合规、时间格式、降序排列、分页链接） |
| **README 更新** | 将 `get_raw()` 改为 `get_inlet_history_temperature()`；说明标准路径优先，OEM 路径仅作参考 |

**改写步骤**：
1. 第 80-113 行 HTTP GET 逻辑改为：`hist_temp = self.client.get_inlet_history_temperature()`
2. 检测返回值：`if hist_temp is None: return "FAIL", []`（标准路径不通）
3. 从 `hist_temp` 对象提取历史数据：`data = hist_temp.historical_inlet_temp` 或类似字段名
4. 保留原有数据验证逻辑（非空、字段合规、时间格式、降序排列）
5. OEM 路径探测改为：`oem_hist = self.client.get_raw("/redfish/v1/Chassis/1/Thermal/InletHistoryTemperature")` 作为参考记录
6. 更新 README.md 第 83 行

**风险与兜底**：
- 若 `get_inlet_history_temperature()` 返回的 `InletHistoryTemperature` 对象字段名与预期不符，需查阅 SDK 源码确认
- 若 BMC 返回 404，`get_inlet_history_temperature()` 返回 None，脚本应记为 FAIL
- 建议保留 OEM 路径探测作为诊断信息，但不影响最终判定

---

### 第三阶段：Event 事件操作（2 个脚本，可并行）

#### 3.1 `bmc/event/event_001_get_service.py`

**原 SDK-GAP**：`EventService.Actions`

**v1.1.0 新接口**：`get_event_service()` 返回 `EventService` 对象（含 `actions` 字段）

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 58 行 `client.get_raw("/redfish/v1/EventService")` |
| **替换前** | `client.get_raw("/redfish/v1/EventService")` 获取原始 JSON |
| **替换后** | `client.get_event_service()` 返回 `EventService` 对象；访问 `event_service.actions` 获取 Actions 块 |
| **异常处理** | 若 BMC 不支持 Actions，`event_service.actions` 为 None；脚本应检测并记为 FAIL |
| **兜底策略** | 若 `get_event_service()` 返回的 EventService 对象中 `actions` 为 None，可保留 `get_raw()` fallback 作为兼容 |
| **判定逻辑** | 保留原有字段完整性检查逻辑（REQUIRED_FIELDS + Actions.SubmitTestEvent 存在性） |
| **README 更新** | 将 `get_raw()⚠️ [SDK-GAP]` 改为 `get_event_service()` |

**改写步骤**：
1. 删除第 53-57 行 `[SDK-GAP]` WARNING 日志
2. 第 58 行改为：`event_service = self.client.get_event_service()`
3. 第 64-70 行改为：从 `event_service` 对象提取字段（而非 dict）
4. 第 72 行改为：`has_submit = event_service.actions and "#EventService.SubmitTestEvent" in event_service.actions`
5. 异常捕获：若 `get_event_service()` 抛异常，记为 FAIL
6. 更新 README.md 第 118 行

**风险与兜底**：
- 若 `event_service.actions` 为 None，脚本应检测并记为 FAIL（或保留 `get_raw()` fallback）
- 建议在改写时验证 v1.1.0 SDK 中 EventService.actions 的结构

---

#### 3.2 `bmc/event/event_009_submit_test_event.py`

**原 SDK-GAP**：`SubmitTestEvent` 及 `AllowableValues`

**v1.1.0 新接口**：`get_event_service()` + `submit_test_event(event_type, ...)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 81 行 `client.get_raw()` 获取 AllowableValues；第 101 行 `client.post()` 执行 SubmitTestEvent |
| **替换前** | `client.get_raw("/redfish/v1/EventService")` 获取 AllowableValues；`client.post(SUBMIT_URI, {"EventType": etype})` 执行 SubmitTestEvent |
| **替换后** | `client.get_event_service()` 获取 EventService 对象；`client.submit_test_event(event_type)` 执行 SubmitTestEvent |
| **异常处理** | `submit_test_event()` 若 BMC 不支持或参数非法，抛 `RedfishValidationError`；脚本应捕获并记为 FAIL |
| **兜底策略** | 若 `get_event_service().actions` 为 None，可保留 `get_raw()` fallback 获取 AllowableValues；若 `submit_test_event()` 不支持，可保留 `client.post()` fallback |
| **判定逻辑** | 保留原有 EventType 枚举验证 + 合法值测试逻辑 |
| **README 更新** | 将 `client.post() + get_raw()⚠️ [SDK-GAP]` 改为 `get_event_service()` + `submit_test_event()` |

**改写步骤**：
1. 删除第 74-80 行 `[SDK-GAP]` WARNING 日志
2. 第 81 行改为：`event_service = self.client.get_event_service()`
3. 第 82-84 行改为：从 `event_service.actions` 提取 AllowableValues（而非 dict）
4. 第 101 行改为：`self.client.submit_test_event(etype)`（替换 `client.post()`）
5. 异常捕获：`RedfishValidationError` 作为 FAIL 处理
6. 更新 README.md 第 123 行

**风险与兜底**：
- 若 `event_service.actions` 为 None，脚本应保留 `get_raw()` fallback
- 若 `submit_test_event()` 不支持，脚本应保留 `client.post()` fallback
- 建议在改写时验证 v1.1.0 SDK 中 EventService.actions 的结构

---

### 第四阶段：Systems 系统操作（3 个脚本，可并行）

#### 4.1 `bmc/systems/systems_003b_physical_drives_check.py`

**原 SDK-GAP**：Storage 仅返回 Link，无 Drive 详情

**v1.1.0 新接口**：`get_drive(odata_id)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 230 行后的 Drive 获取逻辑 |
| **替换前** | `client.get_raw(drive_link.odata_id)` 获取原始 JSON，再用 Drive 模型 parse |
| **替换后** | `client.get_drive(drive_link.odata_id)` 直接返回 Drive 对象 |
| **异常处理** | 若 `get_drive()` 抛异常，脚本应捕获并记为 FAIL（该 Drive 无法获取） |
| **兜底策略** | 若 `get_drive()` 返回的 Drive 对象缺少某些字段，可保留 `get_raw()` fallback 作为兼容 |
| **判定逻辑** | 保留原有 Drive 字段完整性检查逻辑（必要字段 / 非必要字段分层） |
| **README 更新** | 将 `get_storages() + get_raw()⚠️ [SDK-GAP]` 改为 `get_storages()` + `get_drive()` |

**改写步骤**：
1. 在 `_check_drive()` 方法前添加新方法 `_get_drive_from_link()`：
   ```python
   def _get_drive_from_link(self, drive_link):
       """从 Storage.drives Link 获取 Drive 详情"""
       try:
           return self.client.get_drive(drive_link.odata_id)
       except RedfishException as e:
           CommonFunction.print_log("ERROR", f"get_drive({drive_link.odata_id}) 失败：{str(e)}")
           return None
   ```
2. 在调用 `_check_drive()` 前改为：`drive = self._get_drive_from_link(drive_link)`
3. 删除原有 `client.get_raw()` + Drive 模型 parse 逻辑
4. 更新 README.md 第 97 行

**风险与兜底**：
- 若 `get_drive()` 返回的 Drive 对象缺少某些字段（如 `revision`），脚本应检测并记为 FAIL
- 建议在改写时验证 v1.1.0 SDK 中 Drive 模型是否包含所有必要字段

---

#### 4.2 `bmc/systems/systems_004_boot_options_test.py`

**原 SDK-GAP**：BootOptions 集合模型

**v1.1.0 新接口**：`get_boot_options()` / `get_boot_option(option_id)` / `set_boot_option_enabled(option_id, enabled)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 96-111 行新模型检测逻辑；后续 BootOptions 集合遍历逻辑 |
| **替换前** | `client.get_raw(boot_options_id)` 获取 BootOptions 集合；`client.patch()` 修改 BootOptionEnabled |
| **替换后** | `client.get_boot_options()` 返回 `List[BootOption]`；`client.set_boot_option_enabled(option_id, enabled)` 修改并回读 |
| **异常处理** | 若 BMC 不支持新模型，`get_boot_options()` 返回空列表；脚本应检测并记为 FAIL（或 fallback 到旧模型） |
| **兜底策略** | 若 `get_boot_options()` 返回空列表，脚本应保留旧模型逻辑作为 fallback；若 `set_boot_option_enabled()` 不支持，可保留 `client.patch()` fallback |
| **判定逻辑** | 保留原有启动项操作 + 回读验证逻辑；新旧模型均计 PASS |
| **README 更新** | 将 `get_raw()⚠️ [SDK-GAP]` 改为 `get_boot_options()` / `get_boot_option()` / `set_boot_option_enabled()` |

**改写步骤**：
1. 删除第 103-110 行 `[SDK-GAP]` WARNING 日志
2. 第 96-111 行改为：
   ```python
   # 新模型：BootOptions 集合资源
   try:
       boot_options = self.client.get_boot_options()
       if boot_options:
           CommonFunction.print_log("INFO", f"检测到新模型（BootOptions 集合，{len(boot_options)} 项）")
           return "new", boot_options
   except RedfishException as e:
       CommonFunction.print_log("WARNING", f"get_boot_options() 失败：{str(e)}")
   ```
3. 在新模型处理逻辑中，改为：
   ```python
   for option in boot_options:
       self.client.set_boot_option_enabled(option.id, False)  # 禁用
       # 回读验证
       updated = self.client.get_boot_option(option.id)
       if updated.boot_option_enabled != False:
           # FAIL
   ```
4. 异常捕获：`RedfishValidationError` 作为 FAIL 处理
5. 更新 README.md 第 98 行

**风险与兜底**：
- 若 `get_boot_options()` 返回空列表，脚本应 fallback 到旧模型逻辑
- 若 `set_boot_option_enabled()` 不支持，脚本应保留 `client.patch()` fallback
- 建议在改写时同时验证 v1.1.0 SDK 中 BootOption 模型的字段名（如 `boot_option_enabled` vs `BootOptionEnabled`）

---

#### 4.3 `bmc/systems/systems_010a_sel_log_clear_positive.py`

**原 SDK-GAP**：`Log.Actions.ClearLog`

**v1.1.0 新接口**：`get_system_log_service(log_id)` + `clear_system_log(log_id)`

**改写要点**：

| 项目 | 详情 |
|------|------|
| **改动点** | 第 97-107 行 ClearLog Action 执行逻辑 |
| **替换前** | `client.get_raw()` 获取 Log.Actions.ClearLog.target；`client.post()` 执行 ClearLog |
| **替换后** | `client.get_system_log_service(log_id)` 返回 Log 对象（含 `actions` 字段）；`client.clear_system_log(log_id)` 执行 ClearLog |
| **异常处理** | 若 BMC 不支持 ClearLog Action，`clear_system_log()` 抛 `RedfishValidationError`；脚本应捕获并尝试 DELETE Entries 作为 fallback |
| **兜底策略** | 若 `clear_system_log()` 不支持，脚本应保留 DELETE Entries 逻辑作为 fallback |
| **判定逻辑** | 保留原有清除前后条目数量对比逻辑 |
| **README 更新** | 将 `get_system_log_services() + get_raw()⚠️ [SDK-GAP]` 改为 `get_system_log_service()` + `clear_system_log()` |

**改写步骤**：
1. 删除原有 `client.get_raw()` 获取 ClearLog Action 的逻辑
2. 改为：
   ```python
   try:
       self.client.clear_system_log(log_id)
       CommonFunction.print_log("INFO", "ClearLog Action 执行成功")
   except RedfishValidationError as e:
       CommonFunction.print_log("WARNING", f"ClearLog Action 不支持，尝试 DELETE Entries：{str(e)}")
       # fallback 到 DELETE Entries 逻辑
   ```
3. 保留原有 DELETE Entries fallback 逻辑
4. 更新 README.md 第 104 行

**风险与兜底**：
- 若 `clear_system_log()` 不支持，脚本应保留 DELETE Entries 逻辑作为 fallback
- 建议在改写时同时验证 v1.1.0 SDK 中 Log.actions 的结构

---

### 破坏性变更评估

#### 5.1 `bmc/chassis/chassis_004_power_supplies_check.py`

**破坏性变更**：`PowerSupply.line_input_voltage` 类型由 `int` → `float`

**影响范围**：

| 项目 | 详情 |
|------|------|
| **字段** | `PowerSupply.line_input_voltage` |
| **原类型** | `Optional[int]` |
| **新类型** | `Optional[float]` |
| **原因** | 对齐 DMTF Redfish schema；实际 BMC 可能返回 `220.5` 等小数 |
| **脚本影响** | 第 176 行 `isinstance(psu.power_output_watts, (int, float))` 已兼容 float，无需改动 |
| **配置文件影响** | 检查 `conf/bmc/chassis/chassis_004_power_supplies_check.json` 是否有对 `line_input_voltage` 的 int 类型断言 |
| **公共函数影响** | 检查 `func/bmc_account_function.py` 等是否有对 `line_input_voltage` 的 int 类型断言 |

**改写步骤**：
1. 检查脚本第 176 行及周边是否有 `isinstance(x, int)` 严格判断
2. 若有，改为 `isinstance(x, (int, float))`
3. 检查配置文件中是否有对 `line_input_voltage` 的类型假设
4. 检查公共函数中是否有对 `line_input_voltage` 的类型假设
5. 更新 README.md 说明该字段类型变更

**风险与兜底**：
- 若脚本中有 `int(psu.line_input_voltage)` 的强制转换，需改为 `float(psu.line_input_voltage)`
- 若配置文件中有对 `line_input_voltage` 的阈值比较，需确保兼容 float

**当前评估**：
- 脚本第 176 行已使用 `isinstance(psu.power_output_watts, (int, float))`，但未检查 `line_input_voltage`
- 脚本中未发现对 `line_input_voltage` 的直接使用，但需检查是否在其他地方有隐含假设
- **建议**：在改写时添加对 `line_input_voltage` 的显式兼容性检查

---

## 实施阶段与依赖

### 阶段划分

| 阶段 | 脚本 | 依赖 | 预计工作量 |
|------|------|------|----------|
| **第一阶段** | chassis_006a / 007a / 007b | 无 | 3 个脚本，可并行，各 30 分钟 |
| **第二阶段** | chassis_011 / 012 | 第一阶段完成 | 2 个脚本，可并行，各 45 分钟 |
| **第三阶段** | event_001 / 009 | 无 | 2 个脚本，可并行，各 30 分钟 |
| **第四阶段** | systems_003b / 004 / 010a | 无 | 3 个脚本，可并行，各 45 分钟 |
| **破坏性变更** | chassis_004 | 第一阶段完成 | 1 个脚本，15 分钟 |

### 并行策略

- **第一、二、三、四阶段可完全并行**（无依赖）
- **破坏性变更评估可与其他阶段并行**
- **建议顺序**：先完成第一阶段（指示灯操作），再并行第二、三、四阶段

### 测试验证

改写完成后，需对每个脚本进行以下验证：

1. **单脚本测试**：在实际 BMC 上运行，验证 PASS/FAIL 结果与改写前一致
2. **异常处理测试**：验证新接口的异常捕获逻辑正确
3. **兼容性测试**：若保留 fallback，验证 fallback 逻辑正常工作
4. **README 更新验证**：确保 README.md 中的接入度列与脚本实现一致

---

# 任务二：版本管控方案

## 现状勘察

### 2.1 Git 仓库状态

| 项目 | 现状 |
|------|------|
| **是否为 Git 仓库** | ✅ 是（`.git` 目录存在） |
| **远程仓库** | `git@code.devops.xiaohongshu.com:cornerstone/hardware-testing/bmc_autotest.git` |
| **当前分支** | `fengmian`（开发分支） |
| **最近提交** | `aec7074 fix: 将 str \| None 类型注解改为 Optional[str]，提升 Python 3.7+ 兼容性` |
| **提交历史** | 5 条最近提交，涵盖 bug fix / feature / chore |
| **`.gitignore`** | ✅ 存在，已排除 `.codewiz` 等目录 |

### 2.2 SDK 依赖版本锁定现状

**`requirements.txt` 当前内容**：

```
redfish-python-sdk @ git+ssh://git@code.devops.xiaohongshu.com/cornerstone/redfish-python-sdk.git
```

**问题**：
- ❌ **未锁定版本/commit**：每次 `pip install` 都会拉取最新 commit，无法保证可重复性
- ❌ **无版本号记录**：无法追踪当前使用的 SDK 版本
- ❌ **升级不可控**：SDK 更新时无法受控变更

**当前 SDK 版本信息**（根据文档）：
- **版本**：v1.1.0
- **发布日期**：2026-06-15
- **Commit**：3a6d841（推测，需确认）
- **安装路径**：`.venv/lib/python3.10/site-packages/redfish_sdk/`

### 2.3 项目版本管理现状

| 项目 | 现状 |
|------|------|
| **项目版本号** | ❌ 无（无 `__version__` / `setup.py` / `pyproject.toml`） |
| **CHANGELOG** | ❌ 无 |
| **Git Tag** | ❌ 无（无版本标签） |
| **README 版本记录** | ⚠️ 部分（SDK 版本在文档中手工维护） |

### 2.4 脚本与 SDK 版本兼容性追踪

| 项目 | 现状 |
|------|------|
| **脚本元数据** | ❌ 无（脚本头部无 SDK 版本要求） |
| **文档记录** | ⚠️ 部分（README.md 中有 `[SDK-GAP]` 标记，但无版本对应关系） |
| **兼容性矩阵** | ❌ 无 |

---

## 版本锁定策略

### 3.1 SDK 依赖版本锁定

**目标**：精确锁定 SDK 版本/commit，确保可重复性和受控升级。

**方案**：

#### 方案 A：使用 Commit Hash（推荐）

**优点**：
- 最精确，完全可重复
- 不依赖 SDK 版本号的正确性
- 便于追踪具体变更

**缺点**：
- 需要知道 SDK 的确切 commit hash
- 升级时需要查询新 commit hash

**实施**：

```ini
# requirements.txt
redfish-python-sdk @ git+ssh://git@code.devops.xiaohongshu.com/cornerstone/redfish-python-sdk.git@3a6d841
```

**升级流程**：
1. 在 SDK 仓库中确认新版本的 commit hash
2. 更新 `requirements.txt` 中的 commit hash
3. 运行 `pip install -r requirements.txt --upgrade`
4. 验证脚本兼容性
5. 提交 `requirements.txt` 变更

#### 方案 B：使用 Git Tag（备选）

**优点**：
- 易读，版本号清晰
- 便于追踪版本历史

**缺点**：
- 依赖 SDK 仓库的 tag 规范
- 若 SDK 未打 tag，无法使用

**实施**：

```ini
# requirements.txt
redfish-python-sdk @ git+ssh://git@code.devops.xiaohongshu.com/cornerstone/redfish-python-sdk.git@v1.1.0
```

**升级流程**：
1. 在 SDK 仓库中确认新版本的 tag
2. 更新 `requirements.txt` 中的 tag
3. 运行 `pip install -r requirements.txt --upgrade`
4. 验证脚本兼容性
5. 提交 `requirements.txt` 变更

#### 方案 C：使用 `pip-tools` 生成 `requirements.lock`（高级）

**优点**：
- 自动生成完整的依赖树
- 包含所有传递依赖的版本
- 便于审计和重现

**缺点**：
- 需要额外工具
- 维护成本较高

**实施**：

```bash
# 安装 pip-tools
pip install pip-tools

# 创建 requirements.in（高层需求）
cat > requirements.in << EOF
redfish>=3.1.8
redfish-python-sdk @ git+ssh://git@code.devops.xiaohongshu.com/cornerstone/redfish-python-sdk.git@3a6d841
psutil>=5.9.0
requests>=2.28.0
pydantic>=2.0.0
urllib3>=1.26.0,<3.0.0
EOF

# 生成 requirements.lock（完整依赖树）
pip-compile requirements.in -o requirements.lock

# 安装
pip install -r requirements.lock
```

**推荐**：**方案 A（Commit Hash）** 最适合本项目，因为：
- SDK 来自内网 git 仓库，commit hash 最可靠
- 项目规模不大，无需完整依赖树管理
- 升级流程简单清晰

---

### 3.2 SDK 版本升级流程

**标准流程**：

```
1. 确认新 SDK 版本
   ├─ 查看 SDK 仓库的 CHANGELOG / Release Notes
   ├─ 确认新版本的 commit hash 或 tag
   └─ 评估破坏性变更（如本次 v1.1.0 的 PowerSupply.line_input_voltage 类型变更）

2. 更新 requirements.txt
   ├─ 修改 SDK 的 commit hash / tag
   └─ 提交 git commit（如 "chore: upgrade redfish-python-sdk to v1.1.0"）

3. 更新虚拟环境
   ├─ pip install -r requirements.txt --upgrade
   └─ 验证 SDK 版本：python -c "import redfish_sdk; print(redfish_sdk.__version__)"

4. 脚本兼容性评估
   ├─ 检查破坏性变更（如类型变更、方法签名变更）
   ├─ 更新受影响的脚本
   └─ 运行单元测试 / 集成测试

5. 文档更新
   ├─ 更新 docs/redfish_python_sdk_api.md（新接口、变更记录）
   ├─ 更新 README.md（SDK 接入度列、[SDK-GAP] 标记）
   └─ 更新 CHANGELOG.md（项目变更记录）

6. 提交变更
   ├─ git add requirements.txt docs/ README.md CHANGELOG.md bmc/...
   ├─ git commit -m "feat: upgrade redfish-python-sdk to v1.1.0 + adapt scripts"
   └─ git push origin fengmian
```

---

## 项目版本管理

### 4.1 语义化版本（SemVer）

**采用 SemVer 2.0.0 规范**：`MAJOR.MINOR.PATCH`

| 版本号 | 含义 | 示例 |
|--------|------|------|
| **MAJOR** | 破坏性变更（脚本不兼容） | v2.0.0（SDK 大版本升级导致脚本改写） |
| **MINOR** | 新增功能（向后兼容） | v1.1.0（新增脚本、新增 SDK 接口适配） |
| **PATCH** | bug 修复（向后兼容） | v1.0.1（脚本 bug 修复） |

**初始版本**：`v1.0.0`（当前项目状态）

**升级示例**：
- 当前：v1.0.0（基于 SDK v1.0.0）
- 升级后：v1.1.0（基于 SDK v1.1.0，新增脚本改写）
- 若 SDK v2.0.0 有破坏性变更：v2.0.0（脚本大幅改写）

---

### 4.2 CHANGELOG.md

**创建 `CHANGELOG.md`**，记录项目版本历史：

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- New features under development

### Changed
- Changes to existing functionality

### Fixed
- Bug fixes

## [1.1.0] - 2026-06-23

### Added
- Adapt 10 scripts to redfish-python-sdk v1.1.0 new interfaces
  - `chassis_006a_uid_led_positive.py`: Use `set_indicator_led()`
  - `chassis_007a_nvme_led_positive.py`: Use `set_drive_indicator_led()`
  - `chassis_007b_nvme_led_negative.py`: Use `set_drive_indicator_led()`
  - `chassis_011_nvme_power_test.py`: Use `get_drive()` + `drive_reset()`
  - `chassis_012_history_temp_test.py`: Use `get_inlet_history_temperature()`
  - `event_001_get_service.py`: Use `get_event_service()`
  - `event_009_submit_test_event.py`: Use `get_event_service()` + `submit_test_event()`
  - `systems_003b_physical_drives_check.py`: Use `get_drive()`
  - `systems_004_boot_options_test.py`: Use `get_boot_options()` / `set_boot_option_enabled()`
  - `systems_010a_sel_log_clear_positive.py`: Use `get_system_log_service()` + `clear_system_log()`

### Changed
- Upgrade redfish-python-sdk from v1.0.0 to v1.1.0
- Update `docs/redfish_python_sdk_api.md` with v1.1.0 API documentation
- Update `README.md` SDK接入度列，移除 [SDK-GAP] 标记

### Fixed
- Evaluate breaking change: `PowerSupply.line_input_voltage` type change from `int` to `float`

## [1.0.0] - 2026-04-21

### Added
- Initial release with redfish-python-sdk v1.0.0
- 50+ test scripts covering Chassis / Systems / Managers / Account / Session / Event / Update / IPMI / Web
```

---

### 4.3 Git Tag

**为每个版本打 tag**：

```bash
# 创建 tag
git tag -a v1.1.0 -m "Release v1.1.0: Adapt scripts to redfish-python-sdk v1.1.0"

# 推送 tag
git push origin v1.1.0

# 查看所有 tag
git tag -l
```

**Tag 命名规范**：`v<MAJOR>.<MINOR>.<PATCH>`

---

### 4.4 版本号文件

**创建 `bmc/__init__.py`**，定义项目版本：

```python
# bmc/__init__.py
__version__ = "1.1.0"
__sdk_version__ = "1.1.0"  # 对应的 SDK 版本
```

**脚本中可引用**：

```python
from bmc import __version__, __sdk_version__
print(f"bmc_autotest v{__version__} (SDK v{__sdk_version__})")
```

---

## 兼容性追踪机制

### 5.1 脚本元数据

**在每个脚本头部添加元数据注释**：

```python
#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/chassis_006a_uid_led_positive.py -i <bmc_ip> -u <username> -p <password>

SDK 版本要求：>= 1.1.0
SDK 接口依赖：set_indicator_led()
兼容性：
  - v1.1.0+：完全支持
  - v1.0.0：不支持（需用 get_raw() + patch()）

更新历史：
  2026/06/23: 改写为 v1.1.0 set_indicator_led() 接口
  2026/04/22: 初始版本（基于 SDK v1.0.0 get_raw()）
"""
```

---

### 5.2 兼容性矩阵文档

**创建 `docs/COMPATIBILITY.md`**，记录脚本与 SDK 版本的兼容性：

```markdown
# 脚本与 SDK 版本兼容性矩阵

| 脚本 | SDK v1.0.0 | SDK v1.1.0 | SDK v2.0.0 | 备注 |
|------|-----------|-----------|-----------|------|
| chassis_001_drives_check | ✅ | ✅ | ❓ | 无 SDK-GAP |
| chassis_006a_uid_led_positive | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 set_indicator_led() |
| chassis_007a_nvme_led_positive | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 set_drive_indicator_led() |
| chassis_007b_nvme_led_negative | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 set_drive_indicator_led() |
| chassis_011_nvme_power_test | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 get_drive() + drive_reset() |
| chassis_012_history_temp_test | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 get_inlet_history_temperature() |
| event_001_get_service | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 get_event_service() |
| event_009_submit_test_event | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 submit_test_event() |
| systems_003b_physical_drives_check | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 get_drive() |
| systems_004_boot_options_test | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 get_boot_options() / set_boot_option_enabled() |
| systems_010a_sel_log_clear_positive | ⚠️ get_raw | ✅ | ❓ | v1.1.0 改写为 clear_system_log() |

**图例**：
- ✅ 完全支持
- ⚠️ 部分支持（需 get_raw() 绕过）
- ❌ 不支持
- ❓ 待评估
```

---

### 5.3 README.md 版本记录

**在 README.md 顶部添加版本信息**：

```markdown
# **BMC_Function_Test**

_**BMC功能测试脚本**_

> **项目版本**：v1.1.0  
> **SDK 版本**：v1.1.0（redfish-python-sdk）  
> **Python 版本**：>= 3.9  
> **最后更新**：2026-06-23

本项目基于 Redfish 协议对 BMC 进行全功能自动化测试，覆盖 Chassis / Systems / Managers / AccountService / SessionService / EventService / UpdateService / IPMI / Web 等资源

## 版本历史

- **v1.1.0** (2026-06-23)：升级 SDK 到 v1.1.0，改写 10 个脚本使用新接口
- **v1.0.0** (2026-04-21)：初始版本，基于 SDK v1.0.0

详见 [CHANGELOG.md](CHANGELOG.md) 和 [兼容性矩阵](docs/COMPATIBILITY.md)
```

---

### 5.4 CI/CD 集成建议

**若项目引入 CI/CD（如 GitLab CI），可添加以下检查**：

```yaml
# .gitlab-ci.yml
stages:
  - test
  - compatibility

test:
  stage: test
  script:
    - pip install -r requirements.txt
    - python -m pytest tests/  # 若有单元测试
    - python -m bmc.chassis_001_drives_check -i $BMC_IP -u $BMC_USER -p $BMC_PASS

compatibility:
  stage: compatibility
  script:
    - python -c "import redfish_sdk; print(f'SDK version: {redfish_sdk.__version__}')"
    - python -c "from bmc import __version__, __sdk_version__; print(f'Project v{__version__} requires SDK v{__sdk_version__}')"
```

---

## 总结与建议

### 版本管控方案总结

| 项目 | 方案 | 优先级 |
|------|------|--------|
| **SDK 版本锁定** | 使用 Commit Hash（requirements.txt） | 🔴 高 |
| **项目版本号** | 采用 SemVer（bmc/__init__.py） | 🟡 中 |
| **版本历史** | 创建 CHANGELOG.md | 🟡 中 |
| **Git Tag** | 为每个版本打 tag | 🟡 中 |
| **兼容性追踪** | 脚本元数据 + 兼容性矩阵 | 🟡 中 |
| **CI/CD 集成** | 若有 CI/CD 则添加版本检查 | 🟢 低 |

### 实施步骤

**第一步（立即）**：
1. 更新 `requirements.txt`，锁定 SDK commit hash
2. 创建 `bmc/__init__.py`，定义项目版本
3. 创建 `CHANGELOG.md`，记录版本历史

**第二步（脚本改写完成后）**：
1. 更新 `docs/redfish_python_sdk_api.md`
2. 更新 `README.md`，移除 `[SDK-GAP]` 标记
3. 创建 `docs/COMPATIBILITY.md`，记录兼容性矩阵
4. 在脚本头部添加元数据注释
5. 打 Git tag（v1.1.0）

**第三步（可选）**：
1. 若项目有 CI/CD，添加版本检查
2. 建立 SDK 升级流程文档

---

# 附录

## A. 快速参考：改写前后对比

### Chassis 指示灯

```python
# 改写前（v1.0.0）
client.patch(chassis_odata_id, {"IndicatorLED": "Lit"})
raw = client.get_raw(chassis_odata_id)
actual = raw.get("IndicatorLED")

# 改写后（v1.1.0）
client.set_indicator_led("Lit")
chassis = client.get_chassis()
actual = chassis.indicator_led
```

### Drive 指示灯

```python
# 改写前（v1.0.0）
client.patch(drive_odata_id, {"IndicatorLED": "Lit"})
raw = client.get_raw(drive_odata_id)
actual = raw.get("IndicatorLED")

# 改写后（v1.1.0）
client.set_drive_indicator_led(drive_odata_id, "Lit")
drive = client.get_drive(drive_odata_id)
actual = drive.indicator_led
```

### Drive 上下电

```python
# 改写前（v1.0.0）
raw = client.get_raw(drive_odata_id)
reset_target = raw.get("Actions", {}).get("#Drive.Reset", {}).get("target")
client.post(reset_target, {"ResetType": "GracefulShutdown"})

# 改写后（v1.1.0）
client.drive_reset(drive_odata_id, "GracefulShutdown")
```

### EventService

```python
# 改写前（v1.0.0）
raw = client.get_raw("/redfish/v1/EventService")
actions = raw.get("Actions", {})
allowable = actions.get("#EventService.SubmitTestEvent", {}).get("EventType@Redfish.AllowableValues", [])
client.post("/redfish/v1/EventService/Actions/EventService.SubmitTestEvent", {"EventType": "Alert"})

# 改写后（v1.1.0）
event_service = client.get_event_service()
allowable = event_service.actions.get("#EventService.SubmitTestEvent", {}).get("EventType@Redfish.AllowableValues", [])
client.submit_test_event("Alert")
```

### BootOptions

```python
# 改写前（v1.0.0）
raw = client.get_raw("/redfish/v1/Systems/1/BootOptions")
for option in raw.get("Members", []):
    client.patch(option["@odata.id"], {"BootOptionEnabled": False})

# 改写后（v1.1.0）
boot_options = client.get_boot_options()
for option in boot_options:
    client.set_boot_option_enabled(option.id, False)
```

### SEL 日志清除

```python
# 改写前（v1.0.0）
raw = client.get_raw(log_service_odata_id)
clear_target = raw.get("Actions", {}).get("#LogService.ClearLog", {}).get("target")
client.post(clear_target, {})

# 改写后（v1.1.0）
client.clear_system_log(log_id)
```

---

## B. 文件清单

**需创建/修改的文件**：

| 文件 | 操作 | 优先级 |
|------|------|--------|
| `requirements.txt` | 修改（锁定 SDK commit） | 🔴 高 |
| `bmc/__init__.py` | 创建（项目版本） | 🟡 中 |
| `CHANGELOG.md` | 创建（版本历史） | 🟡 中 |
| `docs/COMPATIBILITY.md` | 创建（兼容性矩阵） | 🟡 中 |
| `docs/redfish_python_sdk_api.md` | 修改（已有，更新 v1.1.0 内容） | 🔴 高 |
| `README.md` | 修改（版本信息、接入度列） | 🔴 高 |
| `bmc/chassis/chassis_006a_uid_led_positive.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/chassis/chassis_007a_nvme_led_positive.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/chassis/chassis_007b_nvme_led_negative.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/chassis/chassis_011_nvme_power_test.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/chassis/chassis_012_history_temp_test.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/event/event_001_get_service.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/event/event_009_submit_test_event.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/systems/systems_003b_physical_drives_check.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/systems/systems_004_boot_options_test.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/systems/systems_010a_sel_log_clear_positive.py` | 修改（改写脚本） | 🔴 高 |
| `bmc/chassis/chassis_004_power_supplies_check.py` | 修改（评估破坏性变更） | 🟡 中 |

---

**计划文档完成**。

