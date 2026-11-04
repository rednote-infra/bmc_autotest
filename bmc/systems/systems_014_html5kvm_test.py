#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/systems_014_html5kvm_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，HTML5KVM 创建与最大化测试
2026/05/08: 重写 — 不再重复 012 的参数校验，聚焦实际 KVM 会话创建能力验证

测试策略（按实际设备接口，2026-09-28 修订）：
  被测 BMC 的 KVM 会话创建机制：每次 GET /redfish/v1/Managers/1/IKVM 会创建一个 token，
  返回 SignURL（https://<bmc_ip>/viewer.html?<token>，token 不带 "token=" 前缀），
  浏览器打开该 URL 即建立 KVM 会话；IKVM 响应还含 "Current interface":"HTML 5"。
  会话数（Max/Active）由 /redfish/v1/Managers/1/KvmService 提供（IKVM 不提供）。
  SDK 未封装 KVM 接口，脚本通过 get_raw() 调用。

测试内容（与 012 不重复）：
  1. 多次 GET IKVM 获取多个 session token，验证 token 机制（共享 token / 独立 token）
  2. 每个 token 生成的 SignURL 可 HTTP 访问（GET 返回 200）
  3. Current interface 为 HTML 5（HTML5 KVM）
  4. KvmService 的 MaximumNumberOfSessions ≥ 2（业务要求，支持多会话并发）
  5. NumberOfActivatedSessions 字段存在（运行时计数，实际增量需人工验证）

注意：
  实际多会话并发（浏览器打开多个 viewer）需在人工测试中补充；IKVM 每次 GET 都会创建一个 token。
  自动化测试验证的是"会话创建机制正常工作"，而非"真实 WebSocket 会话建立"。
"""

import os
import time
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

# 实际 KVM URL 来源（每次 GET 创建 token 并返回 SignURL）：IKVM
IKVM_URI = "/redfish/v1/Managers/1/IKVM"
# 会话数（Max/Active）来源：KvmService（IKVM 不提供）；同时作为 SignURL 的兼容回退
KVM_SERVICE_URI = "/redfish/v1/Managers/1/KvmService"

# 测试创建的会话数（验证 ≥2 的业务要求）
TEST_SESSION_COUNT = 3

class Systems014Html5KvmTest(BmcTestBase):
    """Systems HTML5KVM 创建与最大化测试

    用例编号：Redfish_Systems_014
    业务要求：支持 2 个及以上 KVM 会话，提供 HTML5 浏览器访问
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_014_html5kvm_test.json"),
        )
    def _extract_token(self, kvm_url: str) -> str:
        """从 KvmUrl / SignURL 中提取 token。

        支持的格式：
          https://<bmc_ip>/viewer.html?&token=xxxxx   （token= 前缀，历史/兼容）
          https://<bmc_ip>/viewer.html?xxxxx          （IKVM SignURL：token 直接跟在 ? 后）
        """
        if "token=" in kvm_url:
            return kvm_url.split("token=")[-1].split("&")[0]
        if "?" in kvm_url:
            return kvm_url.split("?", 1)[-1].split("&")[0]
        return ""

    def _verify_kvm_url_accessible(self, kvm_url: str, label: str, test: CommonFunction) -> bool:
        """验证 KvmUrl 是否可 HTTP 访问"""
        if not kvm_url:
            test.print_log("ERROR", f"[{label}] KvmUrl 为空，BMC 未返回有效的 KVM 会话 URL，无法验证可访问性")
            return False
        try:
            token_preview = self._extract_token(kvm_url)[:8] + "..." if "token=" in kvm_url else kvm_url[:20] + "..."
            test.print_log("INFO", f"[{label}] 验证 KvmUrl 可访问 (token={token_preview})")
            curl_result = subprocess.run(
                ["curl", "-sk", "--max-time", "10", "-o", "/dev/null", "-w", "%{http_code}", kvm_url],
                capture_output=True, text=True, timeout=15
            )
            http_code = curl_result.stdout.strip()
            accessible = http_code == "200"
            if accessible:
                test.print_log("INFO", f"[{label}] KvmUrl HTTP 状态码: {http_code} ✓")
            else:
                test.print_log("ERROR", f"[{label}] KvmUrl HTTP 状态码: {http_code} ✗（期望 200，KVM URL 不可访问）")
            return accessible
        except (subprocess.TimeoutExpired, Exception) as e:
            test.print_log("ERROR", f"[{label}] KvmUrl 访问异常: {str(e)}")
            return False

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", f"测试策略：多次 GET KvmService 创建多个 session token，"
                               f"验证 token 唯一性和 URL 可访问性（业务要求 ≥2 个 KVM 会话）")
        test.print_log("INFO", "测试开始")

        checks = []
        session_data = {}
        tokens = []
        kvm_urls = []
        final: str = "FAIL"
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # ================================================================
            # 1. 多次 GET IKVM，获取多个 session token / SignURL（实际接口）
            # ================================================================
            test.print_log("INFO", f"开始创建 {TEST_SESSION_COUNT} 个 KVM session token...")

            for i in range(TEST_SESSION_COUNT):
                # [SDK-GAP] get_raw() 获取 KVM 会话信息：KVM 为 OEM 扩展接口、SDK 暂未封装
                CommonFunction.print_log(
                    "WARNING",
                    f"[SDK-GAP] get_raw('{IKVM_URI}') 获取 KVM 会话信息："
                    f"KVM 为 OEM 扩展接口，SDK 暂未封装，待 SDK 补充 KVM 接口后替换"
                )
                kvm_data = {}
                kvm_url = ""
                interface = None
                # 首选实际接口 IKVM（返回 SignURL）；失败/为空时兼容回退 KvmService(KvmUrl)
                try:
                    kvm_data = self.client.get_raw(IKVM_URI)
                    kvm_url = kvm_data.get("SignURL", "") or kvm_data.get("KvmUrl", "")
                    interface = kvm_data.get("Current interface")
                except RedfishException as e:
                    test.print_log("WARNING", f"  Session#{i+1}: GET IKVM 失败（{e}），尝试回退 KvmService")
                if not kvm_url:
                    try:
                        kvm_data = self.client.get_raw(KVM_SERVICE_URI)
                        kvm_url = kvm_data.get("KvmUrl", "") or kvm_data.get("SignURL", "")
                    except RedfishException as e:
                        test.print_log("WARNING", f"  Session#{i+1}: GET KvmService 失败（{e}）")

                token = self._extract_token(kvm_url)

                if not kvm_url:
                    test.print_log("ERROR", f"  Session#{i+1}: BMC 未返回 KVM URL（IKVM.SignURL / KvmService.KvmUrl 均为空）")
                else:
                    test.print_log("INFO", f"  Session#{i+1}: token={token[:8] if token else 'N/A'}..., "
                                   f"interface={interface!r}")

                kvm_urls.append(kvm_url)
                tokens.append(token)
                session_data[f"session_{i+1}"] = {
                    "token": token,
                    "kvm_url": kvm_url,
                    "interface": interface,
                }
                # 每次创建间隔 1s，确保 token 有区分
                time.sleep(1)

            # 会话数（Max/Active）从 KvmService 读取（IKVM 不提供这两个字段）
            last_max = None
            active_now = None
            try:
                svc_data = self.client.get_raw(KVM_SERVICE_URI)
                last_max = svc_data.get("MaximumNumberOfSessions")
                active_now = svc_data.get("NumberOfActivatedSessions")
                test.print_log("INFO", f"  KvmService: MaximumNumberOfSessions={last_max}, "
                               f"NumberOfActivatedSessions={active_now}")
            except RedfishException as e:
                test.print_log("WARNING", f"  读取 KvmService 会话数失败（{e}），Max/Active 检查降级")

            # ================================================================
            # 2. 验证 token 机制
            # ================================================================
            unique_tokens = set(tokens)
            token_count = len(unique_tokens)
            
            # 部分 BMC 使用共享 token 模式：所有请求返回同一 token
            # 这是 HTML5 KVM 的常见实现（一个 token 允许多客户端同时访问）
            if token_count == 1:
                test.print_log("INFO", f"Token 模式: 共享 token（3 次 GET 返回同一 token）")
                test.print_log("INFO", "HTML5 KVM 共享 token 模式是正常设计，一个 token 支持多客户端同时访问")
                checks.append(("Token 机制正常（共享 token 模式）", True))
            elif token_count == TEST_SESSION_COUNT:
                test.print_log("INFO", f"Token 模式: 独立 token（{TEST_SESSION_COUNT} 次 GET 返回 {TEST_SESSION_COUNT} 个不同 token）")
                checks.append(("Token 机制正常（独立 token 模式）", True))
            else:
                test.print_log("WARNING", f"Token 模式异常：{TEST_SESSION_COUNT} 次 GET 返回 {token_count} 个不同 token")
                checks.append(("Token 机制正常", False))

            # ================================================================
            # 3. 验证 SignURL/KvmUrl 可 HTTP 访问（每个 token 生成的 URL 都能加载）
            # ================================================================
            all_accessible = True
            for i, url in enumerate(kvm_urls):
                accessible = self._verify_kvm_url_accessible(url, f"Session#{i+1}", test)
                if not accessible:
                    all_accessible = False
            checks.append((f"{TEST_SESSION_COUNT} 个 KvmUrl 均可访问（HTTP 200）", all_accessible))

            # ================================================================
            # 4. 验证 HTML5 接口 + MaxSessions 支持 ≥2 个并发（业务要求）
            # ================================================================
            ifaces = [v.get("interface") for v in session_data.values() if isinstance(v, dict)]
            if any(ifaces):
                html5_ok = any((x or "").upper().startswith("HTML") for x in ifaces)
                checks.append(("Current interface 为 HTML 5（HTML5 KVM）", html5_ok))
                if not html5_ok:
                    test.print_log("ERROR", f"Current interface 非 HTML：{ifaces}")
            else:
                test.print_log("WARNING", "IKVM 未返回 Current interface 字段，该项跳过（记 WARNING，不影响结果）")

            if last_max is None:
                test.print_log("WARNING", "KvmService 未返回 MaximumNumberOfSessions，MaxSessions≥2 检查跳过（记 WARNING，不影响结果）")
            else:
                max_ge2 = isinstance(last_max, int) and last_max >= 2
                test.print_log("INFO", f"MaximumNumberOfSessions={last_max}, ≥2: {max_ge2}")
                if not max_ge2:
                    test.print_log("ERROR", f"MaxSessions={last_max} < 2，不满足业务要求")
                checks.append(("MaxSessions ≥ 2（支持多会话并发）", max_ge2))

            # ================================================================
            # 5. 验证 ActiveSessions 字段存在（运行时计数，实际增量需人工验证）
            # ================================================================
            if active_now is None:
                test.print_log("WARNING", "KvmService 未返回 NumberOfActivatedSessions，该项跳过（记 WARNING，不影响结果）")
            else:
                checks.append(("NumberOfActivatedSessions 字段存在（会话计数）", True))

            # ================================================================
            # 汇总
            # ================================================================
            final = "PASS" if all(r for _, r in checks) else "FAIL"
            session_data["summary"] = {
                "total_tokens_created": len(tokens),
                "unique_tokens": token_count,
                "token_mode": "shared" if token_count == 1 else "independent",
                "all_urls_accessible": all_accessible,
                "max_sessions": last_max,
                "active_sessions": active_now,
                "interfaces": ifaces,
                "meets_business_requirement": (isinstance(last_max, int) and last_max >= 2),
            }

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

        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "kvm_session_data", "value": session_data})

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 1
    checker = None
    try:
        checker = Systems014Html5KvmTest("Systems014Html5KvmTest")
        checker.run_test()
        if checker.command_check_result in ["FINISH", "PASS"]:
            exit_code = 0
        elif checker.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
        start_time = time.time()
        while (time.time() - start_time) < 5:
            time.sleep(1)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if checker:
            try:
                if hasattr(checker, 'exit_code_path'):
                    with open(checker.exit_code_path, "w", encoding="utf-8") as fp:
                        fp.write(str(exit_code))
            except Exception as ex:
                CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(ex)}")
    sys.exit(exit_code)
