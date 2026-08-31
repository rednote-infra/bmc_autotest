# redfish_python_sdk 接口文档

> **版本**：1.1.1
> **作者**：XiaoHongShu Base Infrastructure
> **安装路径**：`.venv/lib/python3.10/site-packages/redfish_sdk/`
> **本次更新**：v1.1.0 → v1.1.1（2026-06-26）。所有 v1.1.1 新增/变更项均以 `🆕 v1.1.1` 标记，完整清单见文末 [§8 版本变更记录（v1.1.1）](#8-版本变更记录v111)。

---

## 目录

1. [快速开始](#1-快速开始)
2. [RedfishClient — 主入口](#2-redfishclient--主入口)
3. [数据模型（Models）](#3-数据模型models)
4. [异常体系](#4-异常体系)
5. [RedfishResource 枚举](#5-redfishresource-枚举)
6. [接口速查表](#6-接口速查表)
7. [版本变更记录（v1.1.0）](#7-版本变更记录v110)
8. [版本变更记录（v1.1.1）](#8-版本变更记录v111)

---

## 1. 快速开始

```python
import os
from redfish_sdk import RedfishClient

# 推荐通过环境变量传递凭据
client = RedfishClient(
    host=os.environ["BMC_IP"],
    username=os.environ["BMC_USERNAME"],
    password=os.environ["BMC_PASSWORD"],
)

# 查询系统基本信息
system = client.get_system()
print(f"厂商: {system.manufacturer}  型号: {system.model}  电源: {system.power_state}")

# 使用 context manager（推荐，自动关闭连接）
with RedfishClient(host="10.0.0.1", username="Admin", password="xxx") as client:
    system = client.get_system()
    cpus = client.get_processors()
```

---

## 2. RedfishClient — 主入口

```python
from redfish_sdk import RedfishClient
```

### 2.1 初始化

```python
RedfishClient(
    host: str,               # BMC IP 或主机名
    username: str,           # 用户名
    password: str,           # 密码
    verify_ssl: bool = False,      # 是否校验 SSL 证书（BMC 通常用自签名，默认关闭）
    proxy: Optional[str] = None,   # HTTP 代理 URL，如 "http://127.0.0.1:8080"
    connect_timeout: int = 10,     # TCP 连接超时（秒）
    read_timeout: int = 30,        # HTTP 响应读取超时（秒）
    scheme: str = "https",         # 协议，"https" 或 "http"
)
```

### 2.2 生命周期

| 方法 | 说明 |
|------|------|
| `client.close()` | 关闭 HTTP session，释放资源 |
| `client.root()` | 获取 Redfish 根服务文档（`RootService`） |
| `with RedfishClient(...) as client:` | context manager，退出时自动 `close()` |

---

### 2.3 Systems（服务器系统）

#### 查询

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_system(system_id=None)` | `System` | 获取单个系统资源（单系统自动选择） |
| `get_systems()` | `List[System]` | 获取所有系统资源列表 |
| `get_processors(system_id=None)` | `List[Processor]` | 获取 CPU 列表 |
| `get_processor(processor_id, system_id=None)` | `Processor` | 获取指定 CPU |
| `get_memory(system_id=None)` | `List[Memory]` | 获取内存模块（DIMM）列表 |
| `get_memory_device(memory_id, system_id=None)` | `Memory` | 获取指定内存模块 |
| `get_storages(system_id=None)` | `List[Storage]` | 获取存储控制器列表 |
| `get_volumes(storage_id, system_id=None)` | `List[Volume]` | 获取指定存储控制器的卷列表 |
| `get_gpus(system_id=None)` | `List[Gpu]` | 获取 GPU 信息（多厂商 fallback） |
| `get_bios(system_id=None)` | `Bios` | 获取 BIOS 信息 |
| `get_system_log_services(system_id=None)` | `List[Log]` | 获取系统日志服务列表 |
| `get_system_log_entries(log_id, system_id=None)` | `List[LogEntry]` | 获取指定日志服务的日志条目 |
| `get_system_log_service(log_id, system_id=None)` 🆕 v1.1.0 | `Log` | 获取**单个** LogService（含 `#LogService.ClearLog` 等 Actions 块，区别于返回集合的 `get_system_log_services`） |
| `get_boot_options(system_id=None)` 🆕 v1.1.0 | `List[BootOption]` | 获取 BootOptions 集合（现代逐项引导模型；无该链接时返回空列表） |
| `get_boot_option(option_id, system_id=None)` 🆕 v1.1.0 | `BootOption` | 获取单个 BootOption |
| `get_system_fru(system_id=None)` | `Optional[Fru]` | 获取系统 FRU 信息（厂商扩展，可能为 None） |
| `get_pcie_device(odata_id)` | `PCIeDevice` | 按 @odata.id 获取 PCIe 设备 |
| `get_mainboard(system_id=None, chassis_id="1")` | `Optional[MainBoard]` | 获取主板信息（多路径 fallback） |
| `get_manufacturer(system_id=None)` | `str` | 获取服务器厂商名称 |

#### 操作

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `reset(reset_type, system_id=None, skip_power_state_check=False)` | `RedfishResponse` | 系统重置/开关机 |
| `change_boot_source(target, system_id=None, mode="UEFI", enabled="Once")` | `SystemPatchSetting` | 修改引导顺序 |
| `clear_system_log(log_id, system_id=None)` 🆕 v1.1.0 | `None` | 调用 `#LogService.ClearLog` 清空系统日志；BMC 未提供该 Action 时抛 `RedfishValidationError` |
| `set_boot_option_enabled(option_id, enabled, system_id=None)` 🆕 v1.1.0 | `BootOption` | PATCH 切换某 BootOption 的 `BootOptionEnabled` 并回读 |

**`reset_type` 合法值**（由 BMC 决定，常见值）：
`"On"` / `"ForceOff"` / `"GracefulShutdown"` / `"GracefulRestart"` / `"ForceRestart"` / `"ForceOn"`

**`change_boot_source` 参数**：
- `target`：`"Pxe"` / `"Hdd"` / `"Cd"` / `"BiosSetup"` 等（BMC 返回的 `AllowableValues`）
- `mode`：`"UEFI"` 或 `"Legacy"`
- `enabled`：`"Once"` / `"Continuous"` / `"Disabled"`

---

### 2.4 Chassis（机箱）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_chassis(chassis_id="1")` | `Chassis` | 获取机箱信息 |
| `get_drives(chassis_id="1")` | `List[Drive]` | 获取物理磁盘（HDD/SSD/NVMe）列表 |
| `get_network_adapters(chassis_id="1")` | `List[NetworkAdapter]` | 获取网卡列表 |
| `get_pcie_devices(chassis_id="1")` | `List[PCIeDevice]` | 获取 PCIe 设备列表 |
| `get_power(chassis_id="1")` | `Power` | 获取电源信息（PSU、电压等） |
| `get_thermal(chassis_id="1")` | `Thermal` | 获取热感信息（风扇、温度） |
| `get_fan(chassis_id="1")` | `List[Fan]` | 获取风扇信息（新旧 schema 双路径 fallback） |
| `get_power_supplies(chassis_id="1")` | `List[PowerSupply]` | 获取 PSU 列表 |
| `get_fru_service(chassis_id="1")` | `List[dict]` | 获取机箱 OEM FRU 数据（厂商扩展） |
| `get_baseboard_fru(chassis_id="1")` | `Optional[dict]` | 获取主板 FRU 数据（厂商扩展） |
| `get_drive(odata_id)` 🆕 v1.1.0 | `Drive` | 按 `@odata.id` 直接获取**单个** Drive 完整详情（适用于 Storage 只暴露 Link 引用的场景） |
| `drive_reset(drive_odata_id, reset_type)` 🆕 v1.1.0 | `None` | 调用 `#Drive.Reset`（NVMe 上下电 / 电源循环等） |
| `set_indicator_led(state, chassis_id="1")` 🆕 v1.1.0 | `str` | 设置 `Chassis.IndicatorLED`，`state` 须为 `Lit` / `Blinking` / `Off`，否则抛 `RedfishValidationError` |
| `set_drive_indicator_led(drive_odata_id, state)` 🆕 v1.1.0 | `str` | 设置指定盘的 `Drive.IndicatorLED`（取值同上） |
| `get_inlet_history_temperature(chassis_id="1")` 🆕 v1.1.0 | `Optional[InletHistoryTemperature]` | 获取进风口历史温度采样；BMC 不支持或返回 404 时为 `None` |

> **`get_fan()` fallback 路径顺序**：
> 1. `/redfish/v1/Chassis/{id}/ThermalSubsystem/Fans`（新规范）
> 2. `/redfish/v1/Chassis/{id}/Thermal` 中的 `Fans` 数组（旧规范）

---

### 2.5 Managers（BMC 管理器）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_manager(manager_id="1")` | `Manager` | 获取 BMC 管理器信息 |
| `get_manager_log_services(manager_id="1")` | `List[Log]` | 获取 BMC 日志服务列表 |
| `get_manager_log_entries(log_id, manager_id="1")` | `List[LogEntry]` | 获取 BMC 日志条目 |
| `get_network_protocol(manager_id="1")` | `NetworkProtocol` | 获取网络协议配置 |
| `get_manager_ethernet_interfaces(manager_id="1")` | `List[EthernetInterface]` | 获取 BMC 以太网接口列表 |
| `get_host_interfaces(manager_id="1")` | `List[HostInterface]` | 获取 Host Interface 列表 |

---

### 2.6 Account（账号管理）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_accounts()` | `List[Account]` | 获取所有用户账号 |
| `get_roles()` | `List[Role]` | 获取所有角色 |
| `add_account(account: Account)` | `Account` | 创建用户账号 |
| `update_account(username: str, account: Account)` | `Account` | 修改用户账号 |
| `delete_account(username: str)` | `str` | 删除用户账号 |

---

### 2.7 Session（会话管理）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_sessions()` | `List[Session]` | 获取所有活跃会话 |
| `get_session(session_id: str)` | `Session` | 获取指定会话 |
| `create_session(username, password, switch_to_token_auth=False)` | `Session` | 创建会话（登录），`switch_to_token_auth=True` 后续请求改用 Token |
| `delete_session(session_id: str)` | `str` | 删除会话（登出） |

---

### 2.8 Event（事件订阅）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_subscriptions()` | `List[Subscription]` | 获取所有事件订阅（collection-expanded） |
| `get_subscription(id_or_uri)` 🆕 v1.1.1 | `Subscription` | 获取单个订阅（按 ID 或完整 `@odata.id`） |
| `subscribe(destination, event_types=None, context=None, *, protocol="Redfish", http_headers=None, ...)` 🆕 v1.1.1 | `Subscription` | 创建事件订阅（Webhook），支持完整 Redfish EventDestination 字段 |
| `delete_subscription(id_or_uri: str)` 🆕 v1.1.1 | `str` | 删除事件订阅（按 ID 或完整 `@odata.id`） |
| `get_event_service()` 🆕 v1.1.0 | `EventService` | 获取**完整** EventService（含 Actions 块，用于发现 `SubmitTestEvent` 的 target 及其 `AllowableValues`） |
| `submit_test_event(event_type, message=None, message_id=None, severity=None, message_args=None)` 🆕 v1.1.0 | `None` | 调用 `#EventService.SubmitTestEvent` 上报测试事件 |

**`subscribe()` 参数（v1.1.1 扩展）**：
- `destination`：Webhook 接收 URL，如 `"https://my-server/events"`（必填）
- `event_types`：事件类型列表，如 `["Alert", "StatusChange"]`（可选，v1.1.1 起）
- `context`：可选标识字符串（可选，v1.1.1 起）
- `protocol`：通信协议，默认 `"Redfish"`
- `http_headers`：回调 POST 携带的 HTTP 头，支持 `dict` 或 `list[dict]`（🆕 v1.1.1，解决部分厂商需 `X-Auth-Token` 的兼容问题）
- `origin_resources` / `subscription_type` / `registry_prefixes` / `resource_types` / `message_ids` / `delivery_retry_policy` / `event_format_type` / `severities` / `oem_subscription_type`：其他 Redfish EventDestination 可选字段（🆕 v1.1.1）
- `extra`：额外字典字段，浅合并到请求体
- `raw_body`：若提供，完全替换自动生成的请求体

---

### 2.9 Update（固件升级）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_firmware_inventory()` | `List[FirmwareInventory]` | 获取所有固件清单（BIOS、BMC、CPLD 等） |
| `get_client_certificates()` | `List[ClientCertificate]` | 获取固件升级用的客户端证书列表 |
| `simple_update(image_uri, transfer_protocol="HTTP", targets=None, vendor=None, **kwargs)` | `RedfishResponse` | 触发固件升级（自动识别厂商，支持 HTTP/NFS/TFTP） |

**`simple_update()` 说明**：
- 自动检测服务器厂商并使用对应的请求体格式
- 支持主流服务器厂商自动识别
- 返回 `RedfishResponse`，可能包含异步任务引用（通过 `wait_for_task()` 监控）

---

### 2.10 Task（异步任务）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_tasks()` | `List[Task]` | 获取所有任务 |
| `get_task(task_id: str)` | `Task` | 获取指定任务 |
| `wait_for_task(task_id, poll_interval=5, timeout=600)` | `Task` | 轮询任务直到完成或超时 |

---

### 2.11 Registry（消息注册表）

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_registries()` | `List[Registry]` | 获取所有消息注册表 |
| `get_registry(registry_id: str)` | `Registry` | 获取指定消息注册表 |

---

### 2.12 原始 JSON 访问（通用 CRUD）

当 SDK 类型化接口不满足需求时，可直接操作原始 JSON：

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_raw(odata_id: str)` | `dict` | GET 任意 Redfish 资源，返回原始 JSON dict |
| `patch(odata_id: str, body: dict)` | `dict` | PATCH 资源（自动处理 ETag），返回响应 JSON |
| `post(odata_id: str, body: dict = None)` | `dict` | POST 请求，返回响应 JSON |
| `delete(odata_id: str)` | `None` | DELETE 资源 |

```python
# 示例：直接 PATCH 引导顺序
client.patch("/redfish/v1/Systems/1", {
    "Boot": {
        "BootSourceOverrideEnabled": "Once",
        "BootSourceOverrideTarget": "Pxe",
    }
})

# 示例：直接 POST 触发重置
client.post(
    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
    {"ResetType": "GracefulRestart"},
)

# 示例：读取 OEM 自定义字段
data = client.get_raw("/redfish/v1/Managers/1")
oem_info = data.get("Oem", {})
```

---

### 2.13 辅助工具方法

| 方法签名 | 返回类型 | 说明 |
|----------|----------|------|
| `get_etag(path: str)` | `Optional[str]` | 获取资源 ETag（用于并发控制） |
| `get_odata_id(key: RedfishResource)` | `Optional[str]` | 按枚举键查找资源 @odata.id（自动遍历 Root / Systems / Chassis / Managers） |
| `get_resource_odata_id(resource)` | `Optional[str]` | 从任意模型对象中提取 @odata.id |
| `get_all_components_summary(system_id=None, chassis_id="1")` | `dict` | 一次性聚合所有硬件组件信息 |

**`get_all_components_summary()` 返回字典结构**：
```python
{
    "processors":        List[Processor],
    "memory":            List[Memory],
    "storages":          List[Storage],
    "gpus":              List[Gpu],
    "drives":            List[Drive],
    "network_adapters":  List[NetworkAdapter],
    "pcie_devices":      List[PCIeDevice],
    "power":             Power,
    "thermal":           Thermal,
    "fans":              List[Fan],
    "power_supplies":    List[PowerSupply],
    "firmware_inventory": List[FirmwareInventory],
}
```

---

## 3. 数据模型（Models）

所有模型均基于 Pydantic v2，字段使用 `alias` 映射 Redfish JSON 键名，`populate_by_name=True` 允许同时使用 Python 属性名和原始 JSON 键名访问。

### 3.1 公共基类

```python
from redfish_sdk.models.common import Link, Entity, Status, RedfishResponse
```

| 类名 | 关键字段 | 说明 |
|------|----------|------|
| `Link` | `odata_id` | 所有资源的基类，只含 @odata.id |
| `Entity` | `odata_id`, `odata_context`, `odata_type`, `odata_etag`, `id`, `name`, `description` | 完整资源基类，含 ETag 和标准 Redfish 字段 |
| `Status` | `state`, `health`, `health_rollup` | 标准 Redfish Status 对象 |
| `RedfishResponse` | `error`, `odata_id`, `message`, `task_id`, `task_state` | 操作响应体（reset/patch 等） |

---

### 3.2 Systems 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `System` | `manufacturer`, `model`, `serial_number`, `sku`, `part_number`, `system_type`, `power_state`, `bios_version`, `processors` (Link), `memory` (Link), `storage` (Link), `ethernet_interfaces` (Link), `boot` (Boot), `status`, `oem` | `/redfish/v1/Systems/{id}` |
| `Boot` | `boot_source_override_target`, `boot_source_override_enabled`, `boot_source_override_mode`, `allowable_values` | 嵌入于 System |
| `SystemPatchSetting` | `boot` (Boot) | PATCH 请求体 |
| `BootOption` 🆕 v1.1.0 | `boot_option_reference`, `boot_option_enabled`, `uefi_device_path`, `display_name`, `alias` | `/redfish/v1/Systems/{id}/BootOptions/{id}` |
| `BootOptionPatchSetting` 🆕 v1.1.0 | `boot_option_enabled` | PATCH 单个 BootOption 的请求体 |
| `Bios` | `attributes` (dict) | `/redfish/v1/Systems/{id}/Bios` |
| `Processor` | `manufacturer`, `model`, `max_speed_mhz`, `total_cores`, `total_threads`, `processor_type`, `socket`, `status`, `oem` | `/redfish/v1/Systems/{id}/Processors/{id}` |
| `Memory` | `capacity_mib`, `operating_speed_mhz`, `manufacturer`, `part_number`, `serial_number`, `memory_device_type`, `device_locator`, `status`, `oem` | `/redfish/v1/Systems/{id}/Memory/{id}` |
| `Storage` | `drives` (List[Link]), `storage_controllers` (List[StorageController]), `volumes` (Link), `status` | `/redfish/v1/Systems/{id}/Storage/{id}` |
| `StorageController` | `manufacturer`, `model`, `serial_number`, `firmware_version`, `supported_controller_protocols`, `cache_summary`, `status` | 嵌入于 Storage |
| `Volume` | `volume_type`, `capacity_bytes`, `raid_type`, `status` | `/redfish/v1/Systems/{id}/Storage/{id}/Volumes/{id}` |
| `Fru` | `board` (MainBoard), 其余厂商字段 | OEM 扩展 |

---

### 3.3 Chassis 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `Chassis` | `manufacturer`, `model`, `serial_number`, `sku`, `part_number`, `chassis_type`, `indicator_led`, `drives` (Link), `network_adapters` (Link), `power` (Link), `thermal` (Link), `status`, `oem` | `/redfish/v1/Chassis/{id}` |
| `Drive` | `model`, `serial_number`, `manufacturer`, `capacity_bytes`, `protocol`, `media_type`, `indicator_led`, `failure_predicted`, `power_state` 🆕v1.1.0, `actions` 🆕v1.1.0（`#Drive.Reset` 等，弱类型 dict）, `status` | `/redfish/v1/Chassis/{id}/Drives/{id}` |
| `NetworkAdapter` | `manufacturer`, `model`, `serial_number`, `part_number`, `controllers`, `network_ports` (Link), `status` | `/redfish/v1/Chassis/{id}/NetworkAdapters/{id}` |
| `PCIeDevice` | `manufacturer`, `model`, `serial_number`, `device_type`, `pcie_interface`, `status` | `/redfish/v1/Chassis/{id}/PCIeDevices/{id}` 或 System.Links |
| `Power` | `power_control` (List[PowerControl]), `power_supplies` (List[PowerSupply]), `voltages` (List[Voltage]), `redundancy`, `oem` | `/redfish/v1/Chassis/{id}/Power` |
| `PowerSupply` | `manufacturer`, `model`, `serial_number`, `power_capacity_watts`, `power_input_watts`, `power_output_watts`, `line_input_voltage`（⚠️ v1.0.x 起类型由 `int`→`float`，BMC 可能返回 `220.5` 等小数）, `status`, `oem` | 嵌入于 Power |
| `Thermal` | `fans` (List[Fan]), `temperatures` (List[Temperature]), `redundancy`, `inlet_history_temperature` 🆕v1.1.0 (Link), `oem` | `/redfish/v1/Chassis/{id}/Thermal` |
| `Fan` | `reading`, `reading_units`, `member_id`, `physical_context`, `upper_threshold_critical`, `upper_threshold_fatal`, `status` | 嵌入于 Thermal 或独立集合 |
| `Temperature` | `reading_celsius`, `member_id`, `physical_context`, `upper_threshold_critical`, `sensor_number`, `status` | 嵌入于 Thermal |
| `InletHistoryTemperature` 🆕 v1.1.0 | `description`, `historical_inlet_temp` (List[HistoricalInletTempEntry]) | `/redfish/v1/Chassis/{id}/Thermal/InletHistoryTemperature`（厂商扩展，如部分 BMC 的进风口历史温度） |
| `HistoricalInletTempEntry` 🆕 v1.1.0 | `description`, `avg`, `max`, `min`, `time` | 嵌入于 InletHistoryTemperature.HistoricalInletTemp |
| `Gpu` | `manufacturer`, `model`, 其余 OEM 字段 | GraphicsControllers 或 PCIeDevices（多路径 fallback） |
| `MainBoard` | `manufacturer`, `product_name`, `serial_number`, `part_number` 等 FRU 板级信息 | OEM 扩展 |

---

### 3.4 Managers 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `Manager` | `firmware_version`, `manager_type`, `model`, `uuid`, `ethernet_interfaces` (Link), `log_services` (Link), `network_protocol` (Link), `status` | `/redfish/v1/Managers/{id}` |
| `NetworkProtocol` | `hostname`, `fqdn`, `http` (Protocol), `https` (Protocol), `ssh` (Protocol), `snmp` (Protocol), `ipmi` (Protocol), `ntp` (NTP), `syslog` (Syslog), `status` | `/redfish/v1/Managers/{id}/NetworkProtocol` |
| `EthernetInterface` | `mac_address`, `ip_v4_addresses`, `ip_v6_addresses`, `fqdn`, `hostname`, `interface_enabled`, `link_status`, `speed_mbps`, `dhcp_v4`, `dhcp_v6`, `status` | `/redfish/v1/Managers/{id}/EthernetInterfaces/{id}` |
| `HostInterface` | `host_interface_type`, `interface_enabled`, `manager_ethernet_interface` (Link), `status` | `/redfish/v1/Managers/{id}/HostInterfaces/{id}` |

---

### 3.5 Account 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `AccountService` | `accounts` (Link), `roles` (Link), `min_password_length`, `max_password_length`, `account_lockout_threshold`, `account_lockout_duration`, `status` | `/redfish/v1/AccountService` |
| `Account` | `user_name`, `password`, `role_id`, `enabled`, `locked`, `email_address`, `account_types`, `oem` | `/redfish/v1/AccountService/Accounts/{id}` |
| `Role` | `role_id`, `assigned_privileges`, `is_predefined`, `oem_privileges`, `status` | `/redfish/v1/AccountService/Roles/{id}` |

---

### 3.6 Session 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `SessionService` | `sessions` (Link), `session_timeout`, `service_enabled`, `status` | `/redfish/v1/SessionService` |
| `Session` | `user_name`, `session_type`, `oem`, `x_auth_token`（SDK 从响应头中提取） | `/redfish/v1/SessionService/Sessions/{id}` |

---

### 3.7 Event 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `EventService` | `subscriptions` (Link), `delivery_retry_attempts`, `delivery_retry_interval_seconds`, `event_types_for_subscription`, `actions` 🆕v1.1.0（`#EventService.SubmitTestEvent`）, `service_enabled`, `status` | `/redfish/v1/EventService` |
| `Subscription` | `destination`, `event_types`, `context`, `protocol`, `http_headers` 🆕v1.1.1（类型放宽为 `Any`，兼容 dict/list[dict]）, `status` 🆕v1.1.1（类型放宽为 `Any`，兼容 dict/string）, `origin_resources` 🆕v1.1.1, `delivery_retry_policy` 🆕v1.1.1, `message_ids` 🆕v1.1.1, `event_format_type` 🆕v1.1.1, `severities` 🆕v1.1.1, `subscription_type`, `registry_prefixes`, `resource_types` | `/redfish/v1/EventService/Subscriptions/{id}` |

---

### 3.8 Update 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `UpdateService` | `firmware_inventory` (Link), `software_inventory` (Link), `http_push_uri`, `multi_part_http_push_uri`, `service_enabled`, `status` | `/redfish/v1/UpdateService` |
| `FirmwareInventory` | `version`, `manufacturer`, `software_id`, `updateable`, `release_date`, `status` | `/redfish/v1/UpdateService/FirmwareInventory/{id}` |
| `ClientCertificate` | `certificate_string`, `certificate_type`, `issuer`, `subject`, `valid_not_after`, `valid_not_before` | `/redfish/v1/UpdateService/ClientCertificates/{id}` |

---

### 3.9 Task 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `TaskService` | `tasks` (Link), `service_enabled`, `status` | `/redfish/v1/TaskService` |
| `Task` | `task_state`, `task_status`, `percent_complete`, `start_time`, `end_time`, `task_monitor`, `messages`, `status` | `/redfish/v1/TaskService/Tasks/{id}` |

**`task_state` 常见值**：`"New"` / `"Starting"` / `"Running"` / `"Suspended"` / `"Interrupted"` / `"Pending"` / `"Stopping"` / `"Completed"` / `"Killed"` / `"Exception"` / `"Service"`

---

### 3.10 Logs 相关

| 模型 | 关键字段 | 对应端点 |
|------|----------|----------|
| `Log` | `entries` (Link), `max_number_of_records`, `overwrite_policy`, `actions` 🆕v1.1.0（`#LogService.ClearLog`）, `service_enabled`, `status` | `/redfish/v1/Systems/{id}/LogServices/{id}` 或 Managers |
| `LogEntry` | `message`, `message_id`, `severity`, `created`, `entry_type`, `entry_code`, `sensor_type`, `sensor_number`, `status`, `event_timestamp` 🆕v1.1.1, `diagnostic_data_size_bytes` 🆕v1.1.1 | `.../LogServices/{id}/Entries/{id}` |

---

### 3.11 模型校验引擎 check.py（🆕 v1.1.0）

v1.1.0 引入声明式校验引擎 `redfish_sdk/models/check.py`，提供增强版 `Field()` 与 `validate_model()`，让模型字段可携带 Go validator 风格的校验标签。部分内置模型（如 `Processor`、`Drive`、`System.power_state`）已开始使用该标签。

```python
from redfish_sdk.models.check import Field, validate_model

# 模型定义中用增强版 Field（替换 from pydantic import Field）
class Processor(Entity):
    manufacturer: Optional[str] = Field(None, alias="Manufacturer", validate="required,type=str")
    total_cores:  Optional[int] = Field(None, alias="TotalCores",   validate="required,type=int,gt=0")

# 测试代码中显式触发校验
validate_model(processor_instance)
```

**支持的校验规则**：

| 规则 | 含义 | 不满足时的行为 |
|------|------|----------------|
| `required` | 必填（非 None / 非空串） | `warnings.warn`（WARNING，不失败） |
| `type=str/int/float/bool` | 类型校验 | `AssertionError`（FAIL） |
| `gt` / `ge` / `lt` / `le` | 数值上下界 | `AssertionError`（FAIL） |
| `oneof=A B C` | 枚举值（空格分隔） | `AssertionError`（FAIL） |
| `status` | 校验内嵌 `Status` 子对象 | `AssertionError`（FAIL） |
| `list` | 必须为 list 类型 | `AssertionError`（FAIL） |
| `gte_field=other` | 跨字段：当前值 ≥ 另一字段 | `AssertionError`（FAIL） |

> 该引擎的判定取向与本项目测试判定原则一致：`required` 缺失记为 WARNING（不误判产品缺陷），类型 / 值域 / 枚举不符记为 FAIL。

---

## 4. 异常体系

```python
from redfish_sdk import (
    RedfishException,
    RedfishNotFoundError,
    RedfishAuthError,
    RedfishConnectionError,
    RedfishTimeoutError,
    RedfishValidationError,
)
```

| 异常类 | 触发条件 | `status_code` |
|--------|----------|---------------|
| `RedfishException` | 所有 Redfish SDK 错误的基类 | HTTP 状态码 |
| `RedfishNotFoundError` | 资源不存在（HTTP 404） | `404` |
| `RedfishAuthError` | 认证失败（HTTP 401 / 403） | `401` 或 `403` |
| `RedfishConnectionError` | 无法连接 BMC | `0` |
| `RedfishTimeoutError` | 请求超时 | `0` |
| `RedfishValidationError` | 参数非法（如 reset_type 与当前电源状态不兼容） | `400` |

所有异常均携带 `.status_code`、`.message`、`.body` 属性。

---

## 5. RedfishResource 枚举

用于 `get_odata_id()` 方法，自动定位资源路径：

```python
from redfish_sdk import RedfishResource

url = client.get_odata_id(RedfishResource.PROCESSORS)
# → "/redfish/v1/Systems/1/Processors"
```

| 分组 | 枚举值 | Redfish 字段名 |
|------|--------|---------------|
| **RootService 层** | `SYSTEMS` | `Systems` |
| | `CHASSIS` | `Chassis` |
| | `MANAGERS` | `Managers` |
| | `ACCOUNT_SERVICE` | `AccountService` |
| | `SESSION_SERVICE` | `SessionService` |
| | `EVENT_SERVICE` | `EventService` |
| | `UPDATE_SERVICE` | `UpdateService` |
| | `TASK_SERVICE` | `TaskService` |
| | `REGISTRIES` | `Registries` |
| | `CERTIFICATE_SERVICE` | `CertificateService` |
| | `JSON_SCHEMAS` | `JsonSchemas` |
| | `KEY_SERVICE` | `KeyService` |
| | `COMPONENT_INTEGRITY` | `ComponentIntegrity` |
| **System 层** | `PROCESSORS` | `Processors` |
| | `MEMORY` | `Memory` |
| | `STORAGE` | `Storage` |
| | `BIOS` | `Bios` |
| | `ETHERNET_INTERFACES` | `EthernetInterfaces` |
| | `GRAPHICS_CONTROLLERS` | `GraphicsControllers` |
| | `LOG_SERVICES` | `LogServices` |
| | `BOOT_OPTIONS` | `BootOptions` |
| | `SECURE_BOOT` | `SecureBoot` |
| | `VIRTUAL_MEDIA` | `VirtualMedia` |
| | `NETWORK_INTERFACES` 🆕v1.1.0 | `NetworkInterfaces` |
| | `SIMPLE_STORAGE` 🆕v1.1.0 | `SimpleStorage` |
| | `USB_CONTROLLERS` 🆕v1.1.0 | `USBControllers` |
| | `CERTIFICATES` 🆕v1.1.0 | `Certificates` |
| **Chassis 层** | `THERMAL` | `Thermal` |
| | `POWER` | `Power` |
| | `DRIVES` | `Drives` |
| | `NETWORK_ADAPTERS` | `NetworkAdapters` |
| | `PCIE_DEVICES` | `PCIeDevices` |
| | `SENSORS` | `Sensors` |
| **Manager 层** | `NETWORK_PROTOCOL` | `NetworkProtocol` |
| | `HOST_INTERFACES` | `HostInterfaces` |
| | `SERIAL_INTERFACES` | `SerialInterfaces` |
| | `DEDICATED_NETWORK_PORTS` | `DedicatedNetworkPorts` |
| | `SECURITY_POLICY` | `SecurityPolicy` |

---

## 6. 接口速查表

### 查询类接口（只读）

| 接口 | 对应测试场景 |
|------|-------------|
| `get_system()` | 系统基本信息查看（型号、SN、电源状态） |
| `get_processors()` | CPU 信息检查 |
| `get_memory()` | 内存模块检查 |
| `get_storages()` / `get_volumes()` | 存储控制器 / RAID 卷检查 |
| `get_drives()` | 物理磁盘检查（数量、型号、容量、状态） |
| `get_gpus()` | GPU 检查 |
| `get_network_adapters()` | 网卡检查 |
| `get_pcie_devices()` | PCIe 设备检查 |
| `get_thermal()` / `get_fan()` | 热感 / 风扇转速检查 |
| `get_power()` / `get_power_supplies()` | 电源 / PSU 检查 |
| `get_bios()` | BIOS 版本检查 |
| `get_firmware_inventory()` | 固件版本清单 |
| `get_manager()` | BMC 版本、型号 |
| `get_network_protocol()` | 网络协议配置（HTTP/HTTPS/SSH/SNMP/NTP） |
| `get_manager_ethernet_interfaces()` | BMC 网口配置 |
| `get_system_log_entries()` / `get_manager_log_entries()` | SEL / 操作日志 / 审计日志 |
| `get_mainboard()` | 主板 FRU 信息 |
| `get_accounts()` / `get_roles()` | 账号列表 / 角色列表 |
| `get_sessions()` | 当前活跃会话列表 |
| `get_subscriptions()` | 事件订阅列表 |

### 操作类接口（写操作）

| 接口 | 对应测试场景 |
|------|-------------|
| `reset(reset_type)` | 系统重置 / 开关机测试 |
| `change_boot_source(target)` | 引导顺序变更测试 |
| `add_account()` / `update_account()` / `delete_account()` | 账号 CRUD 测试 |
| `create_session()` / `delete_session()` | 登录 / 登出 / Token 认证测试 |
| `subscribe()` / `delete_subscription()` | 事件订阅创建 / 删除测试 |
| `simple_update()` + `wait_for_task()` | 固件升级测试 |
| `clear_system_log()` 🆕v1.1.0 | SEL / 系统日志清除测试 |
| `set_indicator_led()` / `set_drive_indicator_led()` 🆕v1.1.0 | UID / NVMe 定位灯操作测试 |
| `drive_reset()` 🆕v1.1.0 | NVMe 上下电 / Drive 复位测试 |
| `get_boot_options()` / `set_boot_option_enabled()` 🆕v1.1.0 | 现代 BootOptions 引导项测试 |
| `submit_test_event()` 🆕v1.1.0 | 事件测试上报（SubmitTestEvent） |
| `get_inlet_history_temperature()` 🆕v1.1.0 | 进风口历史温度曲线测试 |
| `patch()` / `post()` / `delete()` | 直接操作任意 Redfish 资源 |

---

## 7. 版本变更记录（v1.1.0）

> 发布日期：2026-06-15。本次为**纯增量**升级，v1.0.0 的所有方法签名与返回类型保持不变（唯一的类型调整见下方「破坏性变更」）。`IndicatorLED` 类写操作会校验取值为 `Lit` / `Blinking` / `Off`，非法值抛 `RedfishValidationError`。

### 7.1 新增 Client / Manager API

| API | 说明 |
|-----|------|
| `get_system_log_service(log_id, system_id=None)` | 单个 LogService（含 Actions 块） |
| `clear_system_log(log_id, system_id=None)` | `#LogService.ClearLog` |
| `get_boot_options()` / `get_boot_option(id)` / `set_boot_option_enabled(id, enabled)` | 现代逐项引导模型 |
| `get_drive(odata_id)` | 按 @odata.id 取单个 Drive 详情 |
| `drive_reset(drive_odata_id, reset_type)` | `#Drive.Reset` |
| `set_indicator_led(state, chassis_id="1")` | `Chassis.IndicatorLED` 写 |
| `set_drive_indicator_led(drive_odata_id, state)` | `Drive.IndicatorLED` 写 |
| `get_inlet_history_temperature(chassis_id="1")` | 进风口历史温度采样 |
| `get_event_service()` | 完整 EventService（含 Actions 块） |
| `submit_test_event(event_type, ...)` | `#EventService.SubmitTestEvent` |

### 7.2 新增 / 变更数据模型

- **新增模型**：`BootOption`、`BootOptionPatchSetting`、`InletHistoryTemperature`、`HistoricalInletTempEntry`。
- **新增字段**：`Drive.power_state`、`Drive.actions`、`Log.actions`、`EventService.actions`、`Thermal.inlet_history_temperature`。
- **校验引擎**：新增 `models/check.py`（详见 [§3.11](#311-模型校验引擎-checkpy-v110)）。
- **⚠️ 破坏性变更（v1.0.x 起）**：`PowerSupply.line_input_voltage` 类型由 `Optional[int]` → `Optional[float]`，对齐 DMTF Redfish schema（实际 BMC 可能返回 `220.5` 等小数）。对该字段做过 `isinstance(x, int)` 严格判断的脚本需改为兼容 `float`。

### 7.3 新增 RedfishResource 枚举

`NETWORK_INTERFACES`、`SIMPLE_STORAGE`、`USB_CONTROLLERS`、`CERTIFICATES`。

### 7.4 顶层 re-export

`redfish_sdk` 顶层新增便捷导出：`BootOption`、`Drive`、`EventService`、`Log`、`LogEntry`、`Subscription`。

### 7.5 与 README.md「SDK 已知局限」的对照（待核对脚本）

下表将 v1.1.0 新接口映射到 `README.md` 中以 `get_raw()` + `[SDK-GAP]` 临时占位的脚本，**可据此评估改写为类型化接口**：

| README 中的脚本 | 原 SDK-GAP（缺失项） | v1.1.0 可替换为 |
|-----------------|---------------------|-----------------|
| `chassis/chassis_006a_uid_led_positive.py` | `Chassis.IndicatorLED` 写 | `set_indicator_led(state)` |
| `chassis/chassis_007a_nvme_led_positive.py` | `Drive.IndicatorLED` 写 | `set_drive_indicator_led(odata_id, state)` |
| `chassis/chassis_007b_nvme_led_negative.py` | `Drive.IndicatorLED` 写 | `set_drive_indicator_led(odata_id, state)` |
| `chassis/chassis_011_nvme_power_test.py` | `Drive.Actions` / `Drive.PowerState` | `get_drive()`（含 `power_state`/`actions`）+ `drive_reset()` |
| `chassis/chassis_012_history_temp_test.py` | 历史温度全量 `get_raw` | `get_inlet_history_temperature()`（厂商扩展路径；标准 ThermalSubsystem 仍用 get_raw） |
| `event/event_001_get_service.py` | `EventService.Actions` | `get_event_service().actions` |
| `event/event_009_submit_test_event.py` | `SubmitTestEvent` 及 `AllowableValues` | `get_event_service()` + `submit_test_event()` |
| `systems/systems_003b_physical_drives_check.py` | Storage 仅返回 Link，无 Drive 详情 | `get_drive(odata_id)` |
| `systems/systems_004_boot_options_test.py` | BootOptions 集合模型 | `get_boot_options()` / `get_boot_option()` / `set_boot_option_enabled()` |
| `systems/systems_010a_sel_log_clear_positive.py` | `Log.Actions.ClearLog` | `get_system_log_service()` + `clear_system_log()` |

> ⚠️ 仍未覆盖（继续保持 `get_raw()` + `[SDK-GAP]`）：`systems_014_html5kvm_test.py`（OEM KVM）、`managers/_managers_*`（各类 OEM 服务路径）、`managers_009_timezone_test.py`（`Manager.DateTimeLocalOffset`）等。

---

## 8. 版本变更记录（v1.1.1）

> 发布日期：2026-06-26。本次为**纯增量**小版本升级，v1.1.0 的所有方法签名与返回类型保持不变。后台日志获取逻辑重构为动态路径发现，但对外接口语义完全向后兼容。

### 8.1 日志服务路径发现重构

Systems 和 Managers 的日志相关方法内部逻辑重构：

- 由硬编码 `f"{log_services}/{log_id}/Entries"` 改为**动态发现** `LogServices` 集合成员的真实 `@odata.id`，不再假定 URL 结构。
- 解决了某些厂商非标准路径的兼容性问题。
- `log_id` 参数变为 **Optional**：单服务时自动选中，多服务时抛 `RedfishValidationError`。
- 当父资源未暴露 `LogServices` 链接时，抛出清晰的 `RedfishException(404)` 而非 `AttributeError`。
- 抽取 `_log_helpers.py` 统一 Systems/Managers 日志获取逻辑。

### 8.2 Event 订阅接口扩展

| 新增参数 | 说明 |
|---------|------|
| `http_headers` | 回调 POST 携带的 HTTP 头，支持 dict 或 list[dict]（解决部分厂商需 X-Auth-Token 的兼容问题） |
| `origin_resources` | 过滤订阅的资源范围 |
| `message_ids` | 按 MessageId 过滤 |
| `delivery_retry_policy` / `event_format_type` / `severities` / `oem_subscription_type` | 其他 Redfish EventDestination 可选字段 |
| `extra` / `raw_body` | 额外字段浅合并或完全替换请求体 |

同时新增 `get_subscription(id_or_uri)` 方法。

### 8.3 新增 / 变更数据模型

- **Subscription 模型增强**：`http_headers` 和 `status` 类型放宽为 `Any`；新增 `origin_resources`、`delivery_retry_policy`、`message_ids`、`event_format_type`、`severities` 等字段
- **LogEntry 新增字段**：`event_timestamp`、`diagnostic_data_size_bytes`

### 8.4 与 README.md「SDK 已知局限」的对照

| 涉及脚本 | 原问题 | v1.1.1 改善 |
|---------|--------|------------|
| `event_003_create_subscription.py` | 部分厂商需 HttpHeaders 字段，需降级 BmcHttpClient 手动 POST | `subscribe(http_headers={...})` 原生支持 |
| `event_006_delete_subscription.py` | 同上 | 同上 |
| `event_004_get_subscription.py` | SDK 无 get_subscription() | 新增 `get_subscription(id_or_uri)` |
| `managers_004a/b/c_*_log_check.py` | 硬编码 entries_url + [SDK-GAP] 标记 | 日志路径动态发现 |
