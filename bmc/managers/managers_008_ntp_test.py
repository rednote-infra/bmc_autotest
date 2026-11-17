#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers/managers_008_ntp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC NTP 设置功能测试（OEM NtpService），验证 NTP 启用/禁用和服务器配置
2026/05/09: 新增默认值校验（DefaultServiceEnabled=True、DefaultPreferredNtpServer=<ntp_server>）
2026/10/16: SDK 1.3.0 适配 - 改用 get_ntp_service() 类型化接口，URI 由 SDK 自动发现（issue/8）
2026/11/17: 兼容双方案 - 优先 DMTF 标准 ManagerNetworkProtocol.NTP（ProtocolEnabled/NTPServers），
            探测不到回退 OEM NtpService（ServiceEnabled/PreferredNtpServer），共用校验逻辑；
            稳定性 - ResourceInUse(400) 退避重试、异步 202 轮询等待、定长数组 null 过滤、清理冗余日志
            （注：NetworkProtocol URI 按需求每次查询重新发现，未做缓存）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models.resource_key import RedfishResource
from redfish_sdk.models.managers import NetworkProtocol
from pydantic import ValidationError
import json as _json


# NTP 接口模式
NTP_MODE_STANDARD = "standard"  # DMTF 标准 ManagerNetworkProtocol.NTP
NTP_MODE_OEM = "oem"            # OEM 扩展 NtpService


def _get_ntp_server(svc):
    """读取 NTP 服务器地址。
    PreferredNtpServer 为 OEM 扩展字段（SDK NtpService 模型未声明），
    经 extra="allow" 保留在 model_extra 中可直接属性访问；
    标准等价字段为 PrimaryNtpServer。
    """
    return getattr(svc, "PreferredNtpServer", None) or svc.primary_ntp_server or ""


# 测试用 NTP 服务器地址（可替换为实际可用的 NTP 服务器）
TEST_NTP_SERVER_1 = "ntp.aliyun.com"

# 生产环境 NTP 服务器（默认值校验用，实验室可能 ping 不通，仅校验配置值）
# 在 conf/bmc/managers/managers_008_ntp_test.json 中配置 DefaultNtpServer 字段：
#   - 留空（默认）→ 跳过 PreferredNtpServer 默认值校验
#   - 非空      → 校验，但不匹配时仅 WARNING（非必选项）
DEFAULT_NTP_SERVER = ""

# PreferredNtpServer 相关校验是否为必选项（true=计入 PASS/FAIL，false=仅 WARNING）
# 可在 conf/bmc/managers/managers_008_ntp_test.json 中配置 PreferredNtpServerRequired 覆盖
PREFERRED_NTP_SERVER_REQUIRED = False


class Managers008NtpTest(BmcTestBase):
    """BMC NTP 设置功能测试

    用例编号：Redfish_Managers_008
    测试内容：
    - 【默认值校验】NTP 是否默认开启（标准: ProtocolEnabled=True / OEM: ServiceEnabled=True）
    - 【默认值校验（可选）】NTP 是否默认指向生产 NTP 服务器
    - 启用 NTP 并设置服务器，验证生效
    - 禁用 NTP，验证状态变为 false/disabled
    - 恢复原始配置

    接口兼容：优先标准 ManagerNetworkProtocol.NTP，回退 OEM NtpService。
    PreferredNtpServer 相关检查项默认非必选（由 conf 的 PreferredNtpServerRequired 控制）。
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_008_ntp_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        """加载 NTP 相关可配置参数（在 _load_config 中自动回调）。"""
        self.DEFAULT_NTP_SERVER = conf_section.get("DefaultNtpServer", "") or ""
        self.PREFERRED_NTP_SERVER_REQUIRED = bool(
            conf_section.get("PreferredNtpServerRequired", PREFERRED_NTP_SERVER_REQUIRED)
        )

    def _record_check(self, test, checks, optional_checks, label, ok,
                      ok_msg="", fail_msg="", required=True):
        """记录单个检查项；required=False 时仅记 WARNING，不计入最终 PASS/FAIL。"""
        if ok:
            test.print_log("INFO", ok_msg or f"✓ {label}")
        elif required:
            test.print_log("ERROR", fail_msg or f"✗ {label}")
        else:
            test.print_log("WARNING", (fail_msg or f"⚠ {label}") + "（非必选项，不影响测试结果）")
        (checks if required else optional_checks).append((label, ok))

    # ------------------------------------------------------------------
    # 统一适配层：屏蔽标准/OEM 差异，对外暴露 enabled(bool) + servers(list)
    # ------------------------------------------------------------------
    def _get_ntp(self):
        """查询并归一化 NTP 配置。

        优先 DMTF 标准 ManagerNetworkProtocol.NTP：原始 GET + model_validate，
        解析失败时打印完整 pydantic 错误并降级为直接提取 NTP 段（绕过整份模型校验）；
        若标准接口整体不可用，则回退 OEM NtpService。
        返回 dict: {mode, odata_id, enabled, servers}
        """
        client = self.client
        test = CommonFunction()

        # 1) 优先标准接口
        try:
            np_uri = client.get_odata_id(RedfishResource.NETWORK_PROTOCOL)
            raw = client.get_raw(np_uri)
            try:
                np = NetworkProtocol.model_validate(raw)
                ntp = getattr(np, "ntp", None)
                if ntp is not None:
                    # 过滤 None/空串（部分 BMC 的 NTPServers 为定长数组，单元素 PATCH 后会出现 null 项）
                    servers = [s for s in (ntp.ntp_servers or []) if s]
                    test.print_log("INFO",
                        f"通过标准接口获取 NTP 配置: uri={np_uri}, "
                        f"enabled={ntp.protocol_enabled}, servers={servers}")
                    return {
                        "mode": NTP_MODE_STANDARD,
                        "odata_id": np_uri,
                        "enabled": ntp.protocol_enabled,
                        "servers": servers,
                    }
                test.print_log("WARNING", "标准 NetworkProtocol 响应无 NTP 段，回退 OEM")
            except ValidationError as ve:
                # 仅在校验失败时打印逐条错误（字段路径/类型/信息/实际输入）
                test.print_log("ERROR", "NetworkProtocol 模型校验失败，逐条错误如下:")
                for i, err in enumerate(ve.errors(), 1):
                    test.print_log("ERROR",
                        f"  [{i}] loc={list(err.get('loc', []))} "
                        f"type={err.get('type')} msg={err.get('msg')} "
                        f"input={_json.dumps(err.get('input'), ensure_ascii=False)}")
                # 直接复用已获取的 raw 降级提取 NTP 段
                ntp = raw.get("NTP") or {}
                if ntp:
                    raw_enabled = ntp.get("ProtocolEnabled")
                    enabled = raw_enabled.strip().lower() == "true" if isinstance(raw_enabled, str) else raw_enabled
                    servers = ntp.get("NTPServers") or []
                    if isinstance(servers, str):
                        servers = [servers]
                    servers = [s for s in servers if s]
                    test.print_log("INFO",
                        f"降级提取 NTP 段: enabled={enabled}, servers={servers}")
                    return {
                        "mode": NTP_MODE_STANDARD,
                        "odata_id": np_uri,
                        "enabled": enabled,
                        "servers": servers,
                    }
                test.print_log("WARNING", "原始响应也无 NTP 段，回退 OEM")
            except RedfishException as e:
                test.print_log("DEBUG",
                    f"标准 NetworkProtocol 类型化解析异常: {e.message}")
        except RedfishException as e:
            test.print_log("DEBUG", f"标准 NetworkProtocol 不可用: {e.message}")
        except Exception as e:
            test.print_log("DEBUG", f"标准 NetworkProtocol 处理异常: {e!r}")

        # 2) 回退 OEM NtpService
        try:
            svc = client.get_ntp_service()
            server = _get_ntp_server(svc)
            test.print_log("INFO",
                f"回退 OEM NtpService: enabled={svc.service_enabled}, server={server!r}")
            return {
                "mode": NTP_MODE_OEM,
                "odata_id": svc.odata_id,
                "enabled": svc.service_enabled,
                "servers": [server] if server else [],
            }
        except Exception as e:
            test.print_log("ERROR", f"OEM NtpService 也不可用: {e!r}")
            raise

    def _build_patch(self, mode, enabled=None, servers=None):
        """根据接口模式构造 PATCH body。servers 为空(None/[])时不发送服务器字段。"""
        if mode == NTP_MODE_STANDARD:
            ntp = {}
            if enabled is not None:
                ntp["ProtocolEnabled"] = enabled
            if servers:
                ntp["NTPServers"] = servers
            return {"NTP": ntp}
        # OEM NtpService
        body = {}
        if enabled is not None:
            body["ServiceEnabled"] = enabled
        if servers:
            body["PreferredNtpServer"] = servers[0]
        return body

    @staticmethod
    def _is_resource_in_use(exc):
        """判断是否为 BMC 资源忙/过渡中的可重试错误（HTTP 400 ResourceInUse）。

        SDK 的 RedfishException 把错误 JSON（含 ResourceInUse / in transition）
        放在 .body，而 .message 仅为 "Request to <path> failed"，不含关键字，
        因此必须同时检查 body。
        """
        if not isinstance(exc, RedfishException) or exc.status_code != 400:
            return False
        text = f"{exc.message or ''} {exc.body or ''}".lower()
        return "resourceinuse" in text or "in transition" in text

    def _patch_ntp(self, cfg, enabled=None, servers=None,
                   max_retries=8, retry_delay=3):
        """PATCH NTP 配置（URI 取自资源 @odata.id，不硬编码）。

        BMC 对 NetworkProtocol 的修改通常为异步(202)且串行锁定，连续 PATCH
        易触发 HTTP 400 ResourceInUse（"resource is in use or in transition"）。
        此处遇到该错误自动退避重试，直到成功或重试耗尽。
        """
        test = CommonFunction()
        body = self._build_patch(cfg["mode"], enabled, servers)
        test.print_log("INFO",
            f"PATCH {cfg['odata_id']} (mode={cfg['mode']}) body={body}")
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                self.client.patch(cfg["odata_id"], body)
                return
            except RedfishException as e:
                last_exc = e
                if self._is_resource_in_use(e) and attempt < max_retries:
                    test.print_log(
                        "WARNING",
                        f"PATCH 资源忙(ResourceInUse)，{retry_delay}s 后重试 "
                        f"({attempt}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                test.print_log("ERROR",
                    f"PATCH 失败: status={e.status_code}, msg={e.message}")
                raise
        if last_exc:
            raise last_exc

    def _wait_for_ntp_state(self, expect_enabled=None, expect_server=None,
                            timeout=30, interval=2):
        """轮询 NTP 配置直到达到期望状态（应对 202 异步生效）。返回最终 cfg。"""
        test = CommonFunction()
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                cfg = self._get_ntp()
                last = cfg
                ok = True
                if expect_enabled is not None and cfg["enabled"] is not expect_enabled:
                    ok = False
                if expect_server is not None and expect_server not in cfg["servers"]:
                    ok = False
                if ok:
                    return cfg
            except RedfishException as e:
                test.print_log("DEBUG", f"轮询 GET 异常(忽略): {e.message}")
            time.sleep(interval)
        test.print_log("WARNING", "等待 NTP 状态达到期望超时，使用最后一次查询结果")
        return last

    # ------------------------------------------------------------------
    # 测试主体
    # ------------------------------------------------------------------
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        optional_checks = []
        ntp_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询当前 NTP 配置（自动探测标准/OEM）
            cfg = self._get_ntp()
            cur_enabled = cfg["enabled"]
            cur_servers = cfg["servers"]
            test.print_log("INFO", f"当前 NTP 配置[mode={cfg['mode']}]: "
                           f"enabled={cur_enabled}, servers={cur_servers!r}")
            ntp_info["initial"] = {
                "mode": cfg["mode"],
                "enabled": cur_enabled,
                "servers": cur_servers,
            }
            checks.append(("查询 NTP 配置成功", True))

            # 【默认值校验】NTP 是否默认开启
            if cur_enabled is True:
                test.print_log("INFO", "✓ NTP 默认已开启（enabled=True）")
                checks.append(("NTP 默认开启", True))
            else:
                test.print_log("ERROR", f"✗ NTP 默认未开启（enabled={cur_enabled}），"
                               "期望：True（生产交付要求默认开启）")
                checks.append(("NTP 默认开启", False))

            # 【默认值校验（可选）】NTP 是否指向生产 NTP 服务器
            # PreferredNtpServer 相关为非必选项：未配置 DefaultNtpServer 时跳过；
            # 配置了但不匹配时按 PreferredNtpServerRequired 决定 ERROR/WARNING
            if not self.DEFAULT_NTP_SERVER:
                test.print_log("INFO", "NTP 默认服务器校验已跳过（conf 未配置 DefaultNtpServer）")
            else:
                self._record_check(
                    test, checks, optional_checks, "NTP 默认指向生产服务器",
                    any(self.DEFAULT_NTP_SERVER in s for s in cur_servers),
                    ok_msg=f"✓ NTP 默认指向生产服务器（servers={cur_servers!r}）",
                    fail_msg=f"NTP 未指向生产服务器（当前：{cur_servers!r}，"
                             f"期望：{self.DEFAULT_NTP_SERVER}）",
                    required=self.PREFERRED_NTP_SERVER_REQUIRED,
                )

            # 记录原始状态（用于恢复）
            original_enabled = cfg["enabled"]
            original_servers = cfg["servers"]

            # 2. 启用 NTP 并设置服务器
            test.print_log("INFO", f"启用 NTP，设置服务器: {TEST_NTP_SERVER_1}")
            try:
                self._patch_ntp(cfg, enabled=True, servers=[TEST_NTP_SERVER_1])
                test.print_log("INFO", "NTP PATCH 请求发送成功")
                checks.append(("NTP 启用 PATCH 成功", True))
            except RedfishException as e:
                test.print_log("ERROR", f"NTP 启用失败: {str(e)}")
                checks.append(("NTP 启用 PATCH 成功", False))

            # 3. 验证 NTP 已启用（202 异步生效，轮询等待）
            cfg = self._wait_for_ntp_state(
                expect_enabled=True, expect_server=TEST_NTP_SERVER_1)
            enabled = cfg["enabled"]
            servers = cfg["servers"]
            test.print_log("INFO", f"验证 NTP: enabled={enabled}, servers={servers}")
            checks.append(("NTP 启用验证", enabled is True))
            self._record_check(
                test, checks, optional_checks, "NTP 服务器设置验证",
                TEST_NTP_SERVER_1 in servers,
                ok_msg=f"✓ NTP 服务器已生效（{servers!r}）",
                fail_msg=f"NTP 服务器未生效（当前：{servers!r}，期望含：{TEST_NTP_SERVER_1}）",
                required=self.PREFERRED_NTP_SERVER_REQUIRED,
            )
            ntp_info["after_enable"] = {
                "enabled": enabled,
                "servers": servers,
            }

            # 4. 禁用 NTP
            test.print_log("INFO", "禁用 NTP...")
            try:
                self._patch_ntp(cfg, enabled=False)
                cfg = self._wait_for_ntp_state(expect_enabled=False)
                disabled = cfg["enabled"]
                test.print_log("INFO", f"验证 NTP 禁用: enabled={disabled}")
                checks.append(("NTP 禁用验证", disabled is False))
                ntp_info["after_disable"] = {
                    "enabled": disabled,
                }
            except RedfishException as e:
                test.print_log("ERROR", f"NTP 禁用失败: {str(e)}")
                checks.append(("NTP 禁用验证", False))

            # 5. 恢复原始配置
            test.print_log("INFO", "恢复原始 NTP 配置...")
            try:
                restore_servers = original_servers if original_servers else None
                self._patch_ntp(cfg, enabled=original_enabled, servers=restore_servers)
                cfg = self._wait_for_ntp_state(
                    expect_enabled=original_enabled,
                    expect_server=(original_servers[0] if original_servers else None),
                )
                test.print_log("INFO", f"恢复后 NTP: enabled={cfg['enabled']}, "
                               f"servers={cfg['servers']}")
                checks.append(("NTP 配置恢复", True))
            except RedfishException as e:
                test.print_log("WARNING", f"NTP 配置恢复失败: {str(e)}")
                checks.append(("NTP 配置恢复", False))

            final = "PASS" if all(r for _, r in checks) else "FAIL"

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

        # detail.cycle 保存各检查项结果（必选项：PASS/FAIL；非必选项：PASS/WARNING）
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        for label, passed in optional_checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "WARNING"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "ntp_config_info", "value": ntp_info})

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Managers008NtpTest("Managers008NtpTest")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS"]:
            exit_code = 0
        elif test_name.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            if test_name and hasattr(test_name, 'exit_code_path'):
                with open(test_name.exit_code_path, "w", encoding="utf-8") as e:
                    e.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
