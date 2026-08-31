# BMC 自动化测试包
# 按 Redfish 资源类型和测试类型分层组织

# 各子包对应的测试分组：
#   chassis/   - Chassis 资源测试（硬件信息、传感器、LED、风扇、PCIe）
#   systems/   - Systems 资源测试（CPU、内存、存储、启动项、电源、SEL 日志）
#   managers/  - Managers 资源测试（BMC 配置、网络、日志、远程访问、安全）
#   account/   - AccountService 测试（用户管理、权限、密码策略）
#   session/   - SessionService 测试（会话管理、认证、授权）
#   update/    - UpdateService 测试（固件版本、固件升级）
#   event/     - EventService 测试（事件订阅、事件管理）
#   ipmi/      - IPMI 带外测试（电源、FRU、SEL、用户管理）
#   web/       - Web 接口测试（HTTP API 功能、稳定性压测）
#   stress/    - 稳定性压测（Redfish/IPMI/BMC 重启长时压测）
#   protocol/  - 协议合规测试（DMTF Redfish 规范合规性）
#   legacy/    - 遗留脚本（待重构或归档）

# ============================================================
# 版本信息
# ============================================================
# 项目自身版本（遵循 SemVer）；变更历史见根目录 CHANGELOG.md
__version__ = "1.0.0"

# 当前适配并锁定的 redfish-python-sdk 版本（commit 2bddb5b）
# 与 requirements.txt 中锁定的 commit 保持一致
__sdk_version__ = "1.1.1"
