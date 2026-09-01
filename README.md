# **BMC_Function_Test**

_**BMC功能测试脚本**_

本项目基于 Redfish 协议对 BMC 进行全功能自动化测试，覆盖 Chassis / Systems / Managers / AccountService / SessionService / EventService / UpdateService / IPMI / Web 等资源

## **目录**

- [目录](#目录)
- [使用方法](#使用方法)
- [Chassis资源](#chassis资源)
- [Systems资源](#systems资源)
- [EventService资源](#eventservice资源)
- [UpdateService资源](#updateservice资源)
- [SensionService资源](#sensionservice资源)
- [AccountService资源](#accountservice资源)
- [Managers资源](#managers资源)
- [SDK已知局限（get_raw临时占位）](#sdk已知局限get_raw临时占位)


## **使用方法**

### 环境准备

#### 1. 检查 / 安装 Python 3.9+

本项目要求 **Python >= 3.9**（见 [`requirements.txt`](requirements.txt)），执行前先确认本机版本：

```bash
python3 --version
# 期望输出类似：Python 3.9.x / 3.10.x / 3.11.x ...
```

若版本低于 3.9，需先安装：

- **macOS**（推荐使用 Homebrew）：
  ```bash
  brew install python@3.9
  # 安装后可通过完整路径调用，避免与系统默认 python3 冲突
  /opt/homebrew/bin/python3.9 --version
  ```
- **Linux（Ubuntu/Debian）**：
  ```bash
  sudo apt-get update
  sudo apt-get install -y python3.9 python3.9-venv
  ```
- **Linux（CentOS/RHEL 8 及以上，或 CentOS Stream）**：官方 AppStream 仓库已内置 3.9：
  ```bash
  sudo dnf install -y python39 python39-devel
  # 验证
  python3.9 --version
  ```
- **Linux（CentOS 7）**：默认仓库最高只提供 Python 3.6，需借助第三方仓库（如 IUS）安装 3.9：
  ```bash
  sudo yum install -y https://repo.ius.io/ius-release-el7.rpm
  sudo yum install -y python39u python39u-pip python39u-devel
  # IUS 安装的可执行文件名为 python3.9
  python3.9 --version
  ```
  > 若无法访问 IUS 仓库，也可选择从源码编译安装 Python 3.9（需先安装 `gcc`、`openssl-devel`、`bzip2-devel`、`libffi-devel` 等编译依赖）。
- **Windows**：从 [python.org](https://www.python.org/downloads/) 下载 3.9+ 安装包，安装时勾选 "Add python.exe to PATH"。

#### 2. 创建 Python 3.9 虚拟环境

在**项目根目录**下创建独立虚拟环境（推荐命名为 `venv`），避免污染系统 Python 环境、也避免和其他项目的依赖版本冲突：

```bash
cd /path/to/bmc_autotest

# 若系统装有多个 Python 版本，显式指定 3.9 解释器创建虚拟环境
python3.9 -m venv venv

# 若系统默认 python3 本身就是 3.9+，也可以直接：
# python3 -m venv venv
```

执行成功后，项目根目录下会新增一个 `venv/` 目录，其中包含独立的 Python 解释器、`pip` 及后续安装的第三方库，与系统 Python 完全隔离。

#### 3. 激活虚拟环境

不同操作系统/终端的激活命令不同：

| 系统 / 终端 | 激活命令 |
|:---|:---|
| macOS / Linux（bash、zsh） | `source venv/bin/activate` |
| Windows PowerShell | `venv\Scripts\Activate.ps1` |
| Windows cmd | `venv\Scripts\activate.bat` |

以 macOS/Linux 为例：

```bash
cd /path/to/bmc_autotest
source venv/bin/activate
```

激活成功后，终端提示符前会出现 `(venv)` 前缀，例如：

```
(venv) user@host bmc_autotest %
```

这表示当前 shell 已切换到虚拟环境，此后执行的 `python3` / `pip` 命令都指向 `venv/` 内部的解释器，不会影响系统全局环境。

> **注意**：虚拟环境的激活状态只在当前终端会话内生效。每次打开新的终端窗口/新开一个 SSH 会话执行本项目脚本前，都需要重新 `cd` 到项目根目录并执行 `source venv/bin/activate`。

不再需要虚拟环境时，执行以下命令退出：

```bash
deactivate
```

#### 4. 安装项目依赖

激活虚拟环境后（提示符出现 `(venv)`），安装 [`requirements.txt`](requirements.txt) 中声明的全部依赖：

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

安装完成后可简单验证：

```bash
python3 -c "import redfish_sdk; print('redfish_sdk OK:', redfish_sdk.__version__)"
```

若无报错并打印出版本号，说明依赖安装成功，可以开始执行测试脚本。

---

### 执行单条测试脚本

所有 `bmc/` 下的脚本支持两种执行方式，**推荐使用方式二**。

> 前提：已按上文完成虚拟环境创建与激活。

#### 方式一：直接执行脚本文件

```bash
python3 bmc/<脚本名>.py -i <BMC_IP> -u <用户名> -p <密码>

# 示例
python3 bmc/chassis_001_drives_check.py -i <bmc_ip> -u <username> -p <password>
```

#### 方式二：以模块方式执行（推荐）

```bash
python3 -m bmc.<脚本名> -i <BMC_IP> -u <用户名> -p <密码>

# 示例
python3 -m bmc.chassis_001_drives_check -i <bmc_ip> -u <username> -p <password>
python3 -m bmc.systems_001_processors_check -i <bmc_ip> -u <username> -p <password>
```

模块方式无需关心当前工作目录，只要在项目根目录下即可。

#### 通用参数说明

| 参数            |  简写  | 说明        | 示例              |
|:--------------|:----:|:----------|:----------------|
| `--bmc_ip`    | `-i` | BMC IP 地址 | `-i <bmc_ip>`   |
| `--user_name` | `-u` | BMC 用户名   | `-u <username>` |
| `--password`  | `-p` | BMC 密码    | `-p <password>` |

查看任意脚本的帮助信息：
```bash
python3 -m bmc.<脚本名> --help
```

---

### 使用 bmc_runner 一键批量执行测试（推荐）

单条脚本调试完成后，日常回归/验收测试推荐使用 [`bmc/bmc_runner.py`](bmc/bmc_runner.py) 一键批量执行——它会按 suite 分组自动串行跑完所有脚本，实时采集日志，并生成 Markdown / HTML / Excel 三种格式的可视化测试报告。

#### 基本用法

```bash
# 跑全部 suite（chassis / systems / managers / account / session /
# update / protocol / ipmi / web / event / stress）
python3 -m bmc.bmc_runner -i <bmc_ip> -u <username> -p <password>
```

#### 常用参数

| 参数 | 说明 | 示例 |
|:---|:---|:---|
| `-i` / `--bmc_ip` | BMC IP 地址（必填） | `-i 10.0.0.1` |
| `-u` / `--user_name` | BMC 用户名（必填） | `-u admin` |
| `-p` / `--password` | BMC 密码（必填） | `-p Passw0rd` |
| `--suite` | 指定要执行的分组，逗号分隔；不指定则默认执行除 `stress` 外的全部分组 | `--suite chassis,ipmi` |
| `--only` | 只跑某一个脚本（调试用），自动推断其所属 suite | `--only chassis_001_drives_check` |
| `--nostress` | 屏蔽 stress 压测类脚本（压测默认耗时 12h，日常验证建议加此参数） | `--nostress` |

支持的 suite 分组（见 [`conf/bmc/bmc_runner_suites.json`](conf/bmc/bmc_runner_suites.json)）：
`chassis` / `systems` / `managers` / `account` / `session` / `update` / `protocol` / `ipmi` / `web` / `event` / `stress`

#### 使用示例

```bash
# 1. 跑全部分组（含压测，耗时较长，建议 nohup 放后台执行）
nohup python3 -m bmc.bmc_runner -i <bmc_ip> -u <username> -p <password> > runner_nohup.log 2>&1 &

# 2. 日常回归：跑全部分组但屏蔽压测
python3 -m bmc.bmc_runner -i <bmc_ip> -u <username> -p <password> --nostress

# 3. 只跑 chassis 和 ipmi 两个分组
python3 -m bmc.bmc_runner -i <bmc_ip> -u <username> -p <password> --suite chassis,ipmi

# 4. 只调试单个脚本
python3 -m bmc.bmc_runner -i <bmc_ip> -u <username> -p <password> --only chassis_001_drives_check
```

#### 输出产物

每次执行都会在 `result/bmc/bmc_runner_<时间戳>/` 目录下生成：

| 文件 | 说明 |
|:---|:---|
| `runner.log` | 全量执行日志（含每条脚本的 stdout/stderr，带起止边界标记） |
| `report.md` | Markdown 格式测试报告（分组汇总 + WARNING/FAIL 详情 + 完整结果表） |
| `report.html` | HTML 格式测试报告（含配色统计卡片，适合浏览器直接打开查看） |
| `report.xlsx` | Excel 格式测试报告（汇总 / 测试结果 / WARNING项 / FAIL详情 共 4 个 Sheet；若未安装 `openpyxl` 会自动降级为同名 `.csv`） |
| `summary.json` | 结构化 JSON 汇总结果，便于二次处理或 CI 集成 |

执行结束后终端会打印各产物的完整路径。整体退出码：全部 PASS 且无 ERROR/TIMEOUT 时为 `0`，否则为 `2`，可直接用于 CI 流水线的成败判定。

---

### 故障定位（HTTP DEBUG 日志）

测试结果 FAIL 时，如需查看具体的接口调用 URL 与请求 body 以定位问题：

- **日志文件（始终记录）**：每个用例的全量 HTTP 请求（GET 的 URL、POST/PATCH 的 URL+body、失败响应体）始终写入
  `log/bmc/<用例名>/<用例名>.log`（DEBUG 级别），运行后即可查阅。
- **控制台实时输出（按需开启）**：设置环境变量 `BMC_HTTP_DEBUG=1` 重跑，控制台会逐条实时打印每次接口调用的 URL 与 body：
  ```bash
  BMC_HTTP_DEBUG=1 python3 -m bmc.<脚本名> -i <bmc_ip> -u <username> -p <password>
  ```
  默认关闭以保持控制台输出干净；定位完成后无需该变量即可恢复正常。


## **Chassis资源**

Chassis资源包含一些硬件的基本信息（如硬盘、风扇、网卡、电源模块和主板等）。除此之外，还有一些传感器监控、功耗监控和硬件状态设置等其他功能。

|                脚本名称                |          用例名称           |         用例编号         |         redfish_python_sdk接入度         |
|:----------------------------------:|:-----------------------:|:--------------------:|:-------------------------------------:|
|      chassis_001_drives_check      |     Drives集合资源信息检查      | Redfish_Chassis_001  |             get_drives()              |
|       chassis_002_fans_check       |      Fans集合资源信息检查       | Redfish_Chassis_002  |        get_thermal(),get_fan()        |
| chassis_003_network_adapters_check | NetworkAdapters集合资源信息检查 | Redfish_Chassis_003  |        get_network_adapters()         |
|  chassis_004_power_supplies_check  |  PowerSupplies集合资源信息检查  | Redfish_Chassis_004  |    get_power_supplies(),get_power()   |
|    chassis_005_mainboard_check     |    MainBoard集合资源信息检查    | Redfish_Chassis_005  |            get_mainboard()            |
|   chassis_006a_uid_led_positive    |       UID指示灯操作测试        | Redfish_Chassis_006a | set_indicator_led(), get_chassis() |
|   chassis_006b_uid_led_negative    |      UID指示灯操作反向测试       | Redfish_Chassis_006b |           get_chassis()              |
|   chassis_007a_nvme_led_positive   |       NVMe指示灯操作测试       | Redfish_Chassis_007a | get_drives(), set_drive_indicator_led(), get_drive() |
|   chassis_007b_nvme_led_negative   |      NVMe指示灯操作反向测试      | Redfish_Chassis_007b | get_drives(), get_drive() + client.patch()（反向写直达 BMC） |
|     chassis_009_sensors_check      |     Sensor集合资源信息检查      | Redfish_Chassis_009  | get_thermal(), get_power(), get_power_supplies() |
|   chassis_011_nvme_power_test      |   NVMe上下电测试（#Drive.Reset Action）  | Redfish_Chassis_011  | get_drives(), get_drive(), drive_reset() |
|  chassis_012_history_temp_test     |   历史温度曲线测试（厂商扩展 InletHistoryTemperature）  | Redfish_Chassis_012  | get_inlet_history_temperature() |
|   chassis_013_pcie_devices_check   |   PCIeDevices集合资源信息检查   | Redfish_Chassis_013  |   get_pcie_devices(), get_pcie_device()   |
|       chassis_014_fru_check        |       Fru集合资源信息检查       | Redfish_Chassis_014  | get_baseboard_fru(),get_fru_service() |


## **Systems资源**

Systems资源包含一些硬件的基本信息（主要是CPU、内存和存储信息）。除此之外，还涉及到服务器的Boot启动项设置、服务器电源操作、KVM管理、系统事件日志和一键日志。

|                 脚本名称                  |         用例名称         |         用例编号         |               redfish_python_sdk接入度                |
|:-------------------------------------:|:--------------------:|:--------------------:|:--------------------------------------------------:|
|     systems_001_processors_check      |  Processors集合资源信息检查  | Redfish_Systems_001  |          get_processors(),get_processor()          |
|       systems_002_memory_check        |    Memory集合资源信息检查    | Redfish_Systems_002  |          get_memory(),get_memory_device()          |
| systems_003a_storage_controller_check |    Storage控制器信息检查    | Redfish_Systems_003a |                   get_storages()                   |
|  systems_003b_physical_drives_check   |    Storage物理盘信息检查    | Redfish_Systems_003b | get_storages() + get_drive() |
|     systems_004_boot_options_test     |   BootOptions操作测试    | Redfish_Systems_004  | change_boot_source()（旧）/ get_boot_options(),get_boot_option(),set_boot_option_enabled()（新） |
|    systems_005_power_control_test     |   PowerControl操作测试   | Redfish_Systems_005  |                      reset()                       |
|         systems_006_gpu_check         |     GPU集合资源信息检查      | Redfish_Systems_006  |                     get_gpus()                     |
|       systems_007_sel_log_view        |      SEL日志查看测试       | Redfish_Systems_007  | get_system_log_services(),get_system_log_service(),get_system_log_entries() |
|   systems_008_sel_log_add_negative    |     SEL日志增加反向测试      | Redfish_Systems_008  | get_system_log_services()（Log.entries.odata_id）   |
|  systems_009_sel_log_modify_negative  |     SEL日志修改反向测试      | Redfish_Systems_009  | get_system_log_services(),get_system_log_entries() |
|  systems_010a_sel_log_clear_positive  |     SEL日志清除正向测试      | Redfish_Systems_010a | get_system_log_service(), clear_system_log()（Systems）/ get_raw()+post()（Managers） |
|  systems_010b_sel_log_clear_negative  |     SEL日志清除反向测试      | Redfish_Systems_010b |                         无                          |
|     systems_011_log_download_test     |       一键日志下载测试       | Redfish_Systems_011  |                         无                          |
|                  待定                   |      JBOD硬盘设置测试      | Redfish_Systems_012  |                         无                          |
|     systems_013_bios_check            |       BIOS基本信息检查      | Redfish_Systems_013  |             get_system()（bios_version）              |
|   systems_014_html5kvm_test           |   HTML5KVM创建最大化测试（MaxSessions≥2） | Redfish_Systems_014  | get_raw()⚠️ [SDK-GAP]（OEM KVM接口）                  |


## **EventService资源**

EventService资源主要用来服务器的事件订阅功能。

|                      脚本名称                       |              用例名称               |        用例编号         |              redfish_python_sdk接入度               |
|:--------------------------------------------------:|:--------------------------------:|:-------------------:|:--------------------------------------------------:|
| event_001_get_service.py                           | EventService 资源查询               | Redfish_Event_001   | get_event_service() |
| event_002_get_subscriptions.py                     | EventService 订阅集合查询             | Redfish_Event_002   | get_subscriptions()（全 SDK）+ get_raw()（集合结构验证）           |
| event_003_create_subscription.py                   | EventService 订阅创建               | Redfish_Event_003   | subscribe()（v1.1.1 支持 http_headers，原生兼容 ZTE）+ get_raw()（差集/集合计数）                  |
| event_004_get_subscription.py                      | EventService 订阅资源查询             | Redfish_Event_004   | get_subscription() 🆕v1.1.1 + get_subscriptions()（全 SDK）                        |
| event_006_delete_subscription.py                   | EventService 订阅删除               | Redfish_Event_006   | subscribe()（v1.1.1 支持 http_headers）+ delete_subscription()（v1.1.1 支持 id_or_uri）+ get_raw()（差集/集合计数） |
| event_009_submit_test_event.py                     | EventService 测试事件上报（正向）         | Redfish_Event_009   | get_event_service() + submit_test_event() |


## **UpdateService资源**

UpdateService资源主要用来服务器的固件升级功能。

|                      脚本名称                      |          用例名称           |          用例编号           |      redfish_python_sdk接入度       |
|:------------------------------------------------:|:-----------------------:|:-----------------------:|:----------------------------------:|
| update_001_firmware_version_check | 固件版本基本信息检查（BMC/BIOS/CPLD） | Redfish_UpdateService_001 | get_firmware_inventory() |


## **SensionService资源**

SensionService资源主要用来管理BMC的会话功能。

|                       脚本名称                        |        用例名称        |         用例编号         | redfish_python_sdk接入度 |
|:-------------------------------------------------:|:------------------:|:--------------------:|:---------------------:|
|             session_001_info_test.py              | SessionService信息检查 | Redfish_Session_001  |    get_sessions()     |
|            session_002_create_test.py             |    Session创建测试     | Redfish_Session_002  |   create_session()    |
|             session_003_list_test.py              |   Session列表查询测试    | Redfish_Session_003  |    get_sessions()     |
| session_003a_create_invalid_cred_negative_test.py | 无效凭据创建Session（反向）  | Redfish_Session_003a |   create_session()    |
|  session_003b_create_empty_user_negative_test.py  | 空用户名创建Session（反向）  | Redfish_Session_003b |   create_session()    |
|  session_003c_create_empty_pwd_negative_test.py   |  空密码创建Session（反向）  | Redfish_Session_003c |   create_session()    |
|              session_004_get_test.py              |  单个Session详情查询测试   | Redfish_Session_004  |     get_session()     |
|            session_005_delete_test.py             |    Session删除测试     | Redfish_Session_005  |   delete_session()    |
| session_005a_delete_nonexistent_negative_test.py  |  删除不存在Session（反向）  | Redfish_Session_005a |   delete_session()    |
|   session_005b_get_nonexistent_negative_test.py   |  获取不存在Session（反向）  | Redfish_Session_005b |     get_session()     |


## **AccountService资源**

AccountService资源主要用来管理BMC的用户功能。

> 配置文件：`conf/bmc/account_test.json`
> 公共工具：`func/bmc_account_function.py`（`delete_account_safe` / `update_account_safe`，按数字 ID 操作，兼容 ZTE BMC）

|                    脚本名称                     |           用例名称            |         用例编号         |         redfish_python_sdk接入度          |
|:-------------------------------------------:|:-------------------------:|:--------------------:|:--------------------------------------:|
|         account_001_create_test.py          |       Account 创建测试        | Redfish_Account_001  |             add_account()              |
|          account_002_view_test.py           |       Account 查看测试        | Redfish_Account_002  |             get_accounts()             |
|      account_003a_role_modify_test.py       | Account 权限修改测试（覆盖所有角色边界值） | Redfish_Account_003a | update_account(),update_account_safe() |
|    account_003b_password_modify_test.py     |   Account 密码修改测试——正向边界值   | Redfish_Account_003b | update_account(),update_account_safe() |
|   account_003c_password_negative_test.py    |   Account 密码修改测试——反向边界值   | Redfish_Account_003c | update_account(),update_account_safe() |
|          account_004_role_test.py           |      Account 角色定义查看       | Redfish_Account_004  |              get_roles()               |
|         account_005a_delete_test.py         |     Account 删除测试（正向）      | Redfish_Account_005a | delete_account(),delete_account_safe() |
| account_005b_delete_admin_negative_test.py  |       超级管理员不可删除（反向）       | Redfish_Account_005b | delete_account(),delete_account_safe() |
|        account_006_max_count_test.py        |    Account 最大数量测试（≥8）     | Redfish_Account_006  |             add_account()              |
|      account_007_username_rule_test.py      | Account 用户名格式规则测试（正向边界值）  | Redfish_Account_007  |             get_accounts()             |
|        account_008a_disable_test.py         |     Account 禁用测试（正向）      | Redfish_Account_008a |                   无                    |
| account_008b_disable_admin_negative_test.py |       超级管理员不可禁用（反向）       | Redfish_Account_008b |                   无                    |
|      account_009_online_users_test.py       |    在线活动用户 Session 查询测试    | Redfish_Account_009  |             get_accounts()             |


## **Managers资源**

Managers资源主要用来管理BMC自身的功能。

> **⚠️ 重要：SDK 接口使用说明**
>
> `redfish_sdk` 的 `client.reset()` 方法走的是 **Systems（服务器整机）的 Reset**，
> 内部调用 `POST /redfish/v1/Systems/1/Actions/ComputerSystem.Reset`，**不是 BMC 的 Reset**。
>
> **BMC 重启（Manager.Reset）必须手动调用原生 API：**
> ```python
> client.post("/redfish/v1/Managers/1/Actions/Manager.Reset", {"ResetType": "ForceRestart"})
> ```
>
> 待 SDK 封装 `Manager.Reset` 后再统一改为 SDK 调用。（2026-05-08 确认，枫眠）

> **📌 关于文件名前缀 `_` 的说明**
>
> 以 `_` 开头的脚本依赖 OEM 扩展路径（`get_raw()` + `[SDK-GAP]`），当前不纳入 bmc_runner 执行计划。
> 文件保留在目录中，待 SDK 补充对应接口后恢复。

|                       脚本名称                        |               用例名称                |          用例编号           |               redfish_python_sdk接入度               | bmc_runner |
|:-------------------------------------------------:|:---------------------------------:|:-----------------------:|:---------------------------------------------------:|:----------:|
|  managers_001_info_check.py                       | Manager 自身信息检查                    | Redfish_Managers_001    | get_manager(), get_raw()                            | ✅ |
|  managers_002_network_protocol_check.py           | Manager 网络协议信息检查                  | Redfish_Managers_002    | get_network_protocol()                              | ✅ |
|  managers_003_ethernet_interfaces_check.py        | Manager 以太网接口信息检查（IPv4/MAC/速率/状态） | Redfish_Managers_003    | get_manager_ethernet_interfaces()                   | ✅ |
|  managers_004a_sel_log_check.py                   | SEL 日志结构合规性检查                     | Redfish_Managers_004a   | get_manager_log_services() + get_raw()⚠️ [SDK-GAP]（LogEntry 模型缺 @odata.id/@odata.type 元字段，合规性校验仍需 raw JSON） | ✅ |
|  managers_004b_operatelog_check.py                | 操作日志结构合规性检查                      | Redfish_Managers_004b   | get_manager_log_services() + get_raw()⚠️ [SDK-GAP]（同上） | ✅ |
|  managers_004c_auditlog_check.py                  | 审计日志结构合规性检查                      | Redfish_Managers_004c   | get_manager_log_services() + get_raw()⚠️ [SDK-GAP]（同上） | ✅ |
|  managers_006_port_status_check.py                | 网络协议端口信息只读检查                     | Redfish_Managers_006    | get_manager_network_protocol()                      | ✅ |
|  managers_006b_port_enable_disable.py             | 端口使能/禁用功能测试（SSH/SNMP 可写；HTTP/HTTPS 只读） | Redfish_Managers_006b   | get_manager_network_protocol()（全 SDK）             | ✅ |
|  managers_007_bmc_reset.py                        | BMC 重启功能测试（Manager.Reset）         | Redfish_Managers_007    | client.post()（SDK 无封装，禁用 client.reset()）      | ✅ |
|  managers_009_timezone_test.py                    | 时区设置功能测试（DateTimeLocalOffset）     | Redfish_Managers_009    | get_raw()⚠️ [SDK-GAP]（Manager 模型无 DateTimeLocalOffset） | ✅ |
|  managers_026_default_config_check.py             | BMC 默认配置合规检查（IPv4/IPv6/电源冗余/错峰上电/启动项） | Redfish_Managers_026 | get_raw()⚠️ [SDK-GAP]（OEM/pydantic bug/Redundancy 子对象） | ✅ |
| **以下脚本依赖 OEM 扩展路径，暂不执行（文件名前缀 `_`）** | | | | |
|  _managers_008_ntp_test.py                        | NTP 配置功能测试（OEM 接口）               | Redfish_Managers_008    | get_raw()⚠️ [SDK-GAP]（OEM NtpService 路径）         | ⏸ |
|  _managers_010_syslog_test.py                     | Syslog 配置功能测试                      | Redfish_Managers_010    | get_raw()⚠️ [SDK-GAP]（OEM SyslogService 路径）      | ⏸ |
|  _managers_011_snmp_test.py                       | SNMP 配置结构只读验证                     | Redfish_Managers_011    | get_raw()⚠️ [SDK-GAP]（OEM SnmpService 路径）        | ⏸ |
|  _managers_012_kvm_test.py                        | KVM 功能测试（SessionTimeout 设置）        | Redfish_Managers_012    | get_raw()⚠️ [SDK-GAP]（OEM KvmService 路径）         | ⏸ |
|  _managers_013_lldp_test.py                       | LLDP 设置功能测试（LldpEnabled 启用/禁用）    | Redfish_Managers_013    | get_raw()⚠️ [SDK-GAP]（OEM LldpService 路径）        | ⏸ |
|  _managers_015_power_restore_policy_test.py       | 通电开机策略功能测试（ZTE OEM 路径）           | Redfish_Managers_015    | get_raw()⚠️ [SDK-GAP]（OEM PowerOnStrategy）        | ⏸ |
|  _managers_016_dns_check.py                       | DNS 服务配置只读验证                      | Redfish_Managers_016    | get_raw()⚠️ [SDK-GAP]（OEM DnsService 路径）         | ⏸ |
|  _managers_017_vnc_check.py                       | VNC 服务配置只读验证                      | Redfish_Managers_017    | get_raw()⚠️ [SDK-GAP]（OEM VncService 路径）         | ⏸ |
|  _managers_018_firewall_check.py                  | 防火墙规则只读验证（ActiveStrategy/Rules）   | Redfish_Managers_018    | get_raw()⚠️ [SDK-GAP]（OEM FirewallRules 路径）      | ⏸ |
|  _managers_019_ssl_cert_check.py                  | SSL 证书信息只读验证（Issuer/KeyLength≥2048）| Redfish_Managers_019    | get_raw()⚠️ [SDK-GAP]（OEM HttpsCert 路径）          | ⏸ |
|  _managers_020_virtual_media_check.py             | 虚拟媒体挂载状态只读验证（Members 逐一展开）    | Redfish_Managers_020    | get_raw()⚠️ [SDK-GAP]（OEM VirtualMedia 路径）       | ⏸ |
|  _managers_021_sol_check.py                       | SOL 串口配置只读验证（SerialSource/AllowableValues）| Redfish_Managers_021 | get_raw()⚠️ [SDK-GAP]（OEM SOLSourceControlInfo 路径） | ⏸ |
|  _managers_022_vnc_config_test.py                 | VNC 服务配置读写测试（SessionMode/Timeout，测后恢复）| Redfish_Managers_022 | get_raw()⚠️ [SDK-GAP]（OEM VncService 路径）        | ⏸ |
|  _managers_023_sol_log_test.py                    | SOL 串口日志采集测试（ipmitool sol activate，采集 30s）| Redfish_Managers_023 | get_raw()⚠️ [SDK-GAP]（OEM SOLSourceControlInfo 路径） | ⏸ |
|  _managers_024_snmp_trap_test.py                  | SNMP Trap 测试告警触发（无 TrapServer 时跳过）| Redfish_Managers_024    | get_raw()⚠️ [SDK-GAP]（OEM SnmpService 路径）        | ⏸ |
|  _managers_025_syslog_test.py                     | Syslog 测试告警触发（无 Server 时跳过）       | Redfish_Managers_025    | get_raw()⚠️ [SDK-GAP]（OEM SyslogService 路径）      | ⏸ |


## **UpdateService资源（续）**

|                      脚本名称                      |          用例名称           |          用例编号           |      redfish_python_sdk接入度       |
|:------------------------------------------------:|:-----------------------:|:-----------------------:|:----------------------------------:|
| _update_002_bmc_firmware_update.py | BMC 固件升级测试（SimpleUpdate，HTTP/HTTPS/SFTP） | Redfish_UpdateService_002 | ⏸ 暂缓：待用 simple_update() 重写后启用 |
| _update_003_bios_firmware_update.py | BIOS 固件升级测试（SimpleUpdate，HTTP/HTTPS/SFTP） | Redfish_UpdateService_003 | ⏸ 暂缓：待用 simple_update() 重写后启用 |


## **BmcStress 压测资源**

BmcStress 系列脚本用于 BMC 接口稳定性压测，默认 12h（43200s）。

> 压测启动示例：
> ```bash
> cd /tmp/bmc_autotest && source venv/bin/activate
> nohup python3 -m bmc.bmc_stress_001_redfish_api_stress -i <bmc_ip> -u <username> -p <password> > /tmp/redfish_stress.log 2>&1 &
> nohup python3 -m bmc.bmc_stress_002_ipmi_api_stress   -i <bmc_ip> -u <username> -p <password> > /tmp/ipmi_stress.log 2>&1 &
> nohup python3 -m bmc.bmc_stress_003_bmc_reset_stress  -i <bmc_ip> -u <username> -p <password> > /tmp/reset_stress.log 2>&1 &
> ```

| 脚本名称 | 用例名称 | PASS 标准 |
|:---:|:---:|:---:|
| bmc_stress_001_redfish_api_stress | Redfish API 稳定性压测（默认 12h） | 连接失败次数=0 且 单次响应>2s 次数=0 |
| bmc_stress_002_ipmi_api_stress | IPMI API 稳定性压测（默认 12h） | 连接失败次数=0 且 执行超时(>120s)次数=0 |
| bmc_stress_003_bmc_reset_stress | BMC 热/冷重启压测（ipmitool，默认 12h） | 每轮重启后 Redfish+IPMI 均恢复可用 |


## **IPMI 带外测试**

IPMI 脚本通过带外（OOB）通道验证 BMC 的电源管理、FRU、SEL 日志及用户管理功能。

> 执行方式（模块方式）：
> ```bash
> python3 -m bmc.ipmi_001_power_status       -i <bmc_ip> -u <username> -p <password>
> python3 -m bmc.ipmi_006_user_list          -i <bmc_ip> -u <username> -p <password>
> ```

|              脚本名称               |          用例名称          |      用例编号       |        说明         |
|:----------------------------------:|:------------------------:|:------------------:|:-----------------:|
| ipmi_001_power_status              | 电源状态完整操作测试（on/off/cycle/reset） | RDSV_BMC_182 | 6步操作，含恢复 |
| ipmi_002_power_restore_policy      | 通电策略设置测试（always-off/on/previous） | —            | ipmitool chassis policy |
| ipmi_003_fru_info                  | FRU 信息完整性校验           | RDSV_BMC_185 | 必要组字段校验，PSU 跳过 |
| ipmi_004_sel_log                   | SEL 日志查询测试             | RDSV_BMC_186 | 条目数+字段完整性 |
| ipmi_005_boot_order                | 启动项设置测试（pxe/disk/bios/none） | —       | 降级解析 data byte2 |
| ipmi_006_user_list                 | 用户列表查询测试              | RDSV_BMC_079 | 含权限覆盖 WARNING |
| ipmi_007_user_add                  | 增加用户正向测试              | RDSV_BMC_080 | UID=5 测试槽位 |
| ipmi_008_user_name_rule            | 用户名格式规则测试（正向边界）   | RDSV_BMC_080a | |
| ipmi_009_user_password_rule        | 密码格式规则测试（正向边界）    | RDSV_BMC_080b | 8~12 位含大小写数字特殊字符 |
| ipmi_010_user_delete               | 删除用户测试（含超管保护验证）   | RDSV_BMC_081 | 超管不可删降级 WARNING |
| ipmi_011_user_modify               | 修改用户名/密码/权限测试       | RDSV_BMC_082 | 覆盖 USER/OPERATOR/ADMINISTRATOR |
| ipmi_012_user_disable              | 禁用用户测试（含超管保护验证）   | RDSV_BMC_083 | 超管不可禁降级 WARNING |
| ipmi_013_user_session              | 在线用户 Session 查询测试     | RDSV_BMC_084 | create_session 后查询 |


## **BMC Web 自动化测试**

Web 脚本通过 Redfish Session（X-Auth-Token）模拟 Web 浏览器行为，验证 BMC Web 界面的核心功能。
基类：`func/web_client.py`（`BmcWebClient`，基于 `urllib.request`，无第三方依赖）

> 执行方式（模块方式）：
> ```bash
> python3 -m bmc.web_001_login_session       -i <bmc_ip> -u <username> -p <password>
> python3 -m bmc.web_016_http_stress         -i <bmc_ip> -u <username> -p <password>
> ```

|              脚本名称               |          用例名称          |      覆盖需求编号      |       说明        |
|:----------------------------------:|:------------------------:|:------------------:|:-----------------:|
| web_001_login_session              | 登录/会话/登出/Token失效/反向拒绝 | RDSV_BMC_001,002 | Token失效降级WARNING |
| web_002_hardware_info              | 8类硬件资源 URI 可访问+必要字段 | RDSV_BMC_003~010 | CPU动态取集合，GPU可选 |
| web_003_firmware_version           | FirmwareInventory BMC/BIOS 版本检查 | RDSV_BMC_011 | |
| web_004_power_control              | PowerState + AllowableResetTypes（只查不操作） | RDSV_BMC_050,051 | |
| web_005_power_restore_policy       | 通电策略标准+OEM双路查询     | RDSV_BMC_061,062 | |
| web_006_boot_order                 | 启动设备验证（AllowableValues/BootOptions 双路） | RDSV_BMC_063 | 关键字模糊匹配 |
| web_007_uid_control                | UID灯 PATCH开→验证→PATCH关恢复 | RDSV_BMC_066 | 带 If-Match ETag |
| web_008_history_curve              | 功率/温度历史（PowerControl + OEM快照降级） | RDSV_BMC_068,070 | |
| web_009_user_management            | 用户 POST新建→禁用→验证→DELETE清理 | RDSV_BMC_080~083 | 带 If-Match ETag |
| web_010_sensor_info                | 温度/风扇/电压/PSU 传感器条数检查 | RDSV_BMC_071,072 | |
| web_011_sel_log                    | SEL 条目数+前3条字段完整性   | RDSV_BMC_100 | |
| web_012_one_click_log              | 一键日志（LogServices.ExportLogs + OEM降级） | RDSV_BMC_110 | 返回204即PASS |
| web_013_kvm_check                  | KVM 服务可达 + MaximumNumberOfSessions≥1 | RDSV_BMC_131 | 别名兼容 |
| web_014_config_export              | BMC/BIOS 配置可查询验证     | RDSV_BMC_137 | |
| web_015_lang_switch                | Accept-Language zh/en 双语服务可达 | RDSV_BMC_022 | |
| web_016_http_stress                | 默认300s HTTP 稳定性压测    | RDSV_BMC_021 | 失败=0且无慢请求(>5s) |


## **SDK已知局限（get_raw临时占位）**

> 以下场景因 `redfish_python_sdk` 当前版本（v1.1.1）的数据模型尚未封装对应字段，
> 暂时使用 `client.get_raw()` 访问原始 JSON，并在运行时输出 `[SDK-GAP]` WARNING 日志。
> **待 SDK 补充相应字段后应优先替换为类型化接口。**
>
> ✅ **v1.1.0 / v1.1.1 已解决并完成改写**（已移出本表）：`chassis_006a` / `007a` / `007b` / `011` / `012`、
> `event_001` / `009`、`systems_003b` / `004` / `010a`（详见 [CHANGELOG.md](CHANGELOG.md)）。
> 其中 `chassis_007b` 反向写非法值仍刻意使用 `client.patch()` 直达 BMC（绕过 SDK 本地校验，
> 确保真正验证 BMC 服务端拒绝能力），属有意设计而非 SDK 局限。

| 涉及脚本 | 缺失字段 | 期望 SDK 接口 |
|---------|---------|-------------|
| `systems/systems_014_html5kvm_test.py` | ZTE OEM KVM 接口（`/redfish/v1/Managers/1/KvmService`） | SDK 封装 KVM 接口后替换 |

> 以下 managers suite 脚本因依赖 OEM 扩展路径，文件名已加 `_` 前缀暂不执行，待 SDK 补充后恢复：

| 涉及脚本 | OEM URI | 期望 SDK 接口 |
|---------|---------|-------------|
| `managers/_managers_003_ethernet_interfaces_check.py` | `/redfish/v1/Managers/1/EthernetInterfaces`（pydantic bug） | SDK 修复 `EthernetInterface.ip_v4_addresses` 解析后替换 |
| `managers/_managers_008_ntp_test.py` | `/redfish/v1/Managers/1/NtpService` | `get_ntp_service()` 或类似封装 |
| `managers/_managers_010_syslog_test.py` | `/redfish/v1/Managers/1/SyslogService` | `get_syslog_service()` |
| `managers/_managers_011_snmp_test.py` | `/redfish/v1/Managers/1/SnmpService` | `get_snmp_service()` |
| `managers/_managers_012_kvm_test.py` | `/redfish/v1/Managers/1/KvmService` | `get_kvm_service()` |
| `managers/_managers_013_lldp_test.py` | `/redfish/v1/Managers/1/LldpService` | `get_lldp_service()` |
| `managers/_managers_015_power_restore_policy_test.py` | `/redfish/v1/Systems/1` OEM.Public.PowerOnStrategy | `get_system().oem.power_on_strategy` |
| `managers/_managers_016_dns_check.py` | `/redfish/v1/Managers/1/DnsService` | `get_dns_service()` |
| `managers/_managers_017_vnc_check.py` | `/redfish/v1/Managers/1/VncService` | `get_vnc_service()` |
| `managers/_managers_018_firewall_check.py` | `/redfish/v1/Managers/1/SecurityService/FirewallRules` | `get_firewall_rules()` |
| `managers/_managers_019_ssl_cert_check.py` | `/redfish/v1/Managers/1/SecurityService/HttpsCert` | `get_https_cert()` |
| `managers/_managers_020_virtual_media_check.py` | `/redfish/v1/Managers/1/VirtualMedia` | `get_virtual_media()` |
| `managers/_managers_021_sol_check.py` | `/redfish/v1/Managers/1/SOLSourceControlInfo` | `get_sol_source_control_info()` |
| `managers/_managers_022_vnc_config_test.py` | `/redfish/v1/Managers/1/VncService` | `get_vnc_service()` |
| `managers/_managers_023_sol_log_test.py` | `/redfish/v1/Managers/1/SOLSourceControlInfo` | `get_sol_source_control_info()` |
| `managers/_managers_024_snmp_trap_test.py` | `/redfish/v1/Managers/1/SnmpService` | `get_snmp_service()` |
| `managers/_managers_025_syslog_test.py` | `/redfish/v1/Managers/1/SyslogService` | `get_syslog_service()` |

> 以下 managers suite 脚本有 `[SDK-GAP]` 但仍纳入测试（get_raw 已加 WARNING 日志）：

| 涉及脚本 | 缺失字段 | 原因 |
|---------|---------|------|
| `managers/managers_004a/004b/004c_*_log_check.py` | `LogEntry` 模型缺 `@odata.id`/`@odata.type` 元字段（v1.1.1 已消除硬编码 entries_url，动态发现路径） | 合规性校验依赖原始 JSON，保留 get_raw 并标注 |
| `managers/managers_009_timezone_test.py` | `Manager.DateTimeLocalOffset` / `Manager.DateTime` | SDK `Manager` 模型无此字段 |
| `managers/managers_026_default_config_check.py` | OEM.Public.PowerOnDelayEnabled / pydantic bug / Redundancy.mode 等 | 默认配置合规检查，场景独立 |

> 识别方式：运行时日志中搜索 `[SDK-GAP]` 关键字即可定位所有临时占位点。
