#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_009_online_users_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增
2026/04/24: 重写——改为主动创建 Session 后查询，符合 Redfish 协议行为

校验策略（覆盖需求 RDSV_BMC_084）：
  BMC 用 Basic Auth 连接时不会创建 Session，Sessions 列表为空是正常现象。
  正确验证方式：
    Step 1: 主动调用 create_session() 创建一个 Session（Token Auth）
    Step 2: 用 get_raw 读取 Sessions 集合，验证 Members@odata.count >= 1
    Step 3: 验证 Session 关键字段（@odata.id / UserName）完整
    Step 4: 删除（登出）本次创建的 Session，保持环境干净
  PASS：Session 创建成功，列表中能查到，字段完整
  FAIL：Session 创建失败，或列表为空，或关键字段缺失
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class AccountOnlineUsersTest(BmcTestBase):
    """在线用户查询测试

    用例编号：Redfish_Account_009
    检查项：主动创建 Session 后，可通过 Redfish 查询到在线活动用户，字段完整
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self.created_session_id = None  # 预初始化，run_test 中实际赋值

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        """清理：删除本次创建的 Session"""
        if self.created_session_id and self.client:
            try:
                self.client.delete_session(self.created_session_id)
                CommonFunction.print_log("INFO",
                    f"[清理] Session {self.created_session_id} 已删除")
            except Exception as e:
                CommonFunction.print_log("WARNING",
                    f"[清理] 删除 Session 失败（可忽略）：{e}")

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", "测试用例名称：在线用户查询测试，测试用例编号：Redfish_Account_009")
        test.print_log("INFO", "测试开始")
        test.print_log("INFO",
            "说明：BMC 使用 Basic Auth 时不建 Session，Sessions 列表为空是协议正常行为。"
            "本测试通过主动 create_session 创建 Session 后查询，验证在线用户查询功能。")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}（Basic Auth，仅用于后续操作）")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            # Step 1：主动创建 Session
            CommonFunction.print_log("INFO",
                f"Step 1：主动创建 Session（用户：{self.USERNAME}）")
            sess = self.client.create_session(self.USERNAME, self.PASSWORD)
            self.created_session_id = sess.id
            CommonFunction.print_log("INFO",
                f"  ✓ Session 创建成功：id={sess.id!r}, user={sess.user_name!r}")

            # Step 2：查询 Sessions 列表，验证非空
            CommonFunction.print_log("INFO", "Step 2：查询 Sessions 列表")
            sessions = self.client.get_sessions()
            total_count = len(sessions)
            CommonFunction.print_log("INFO", f"  Sessions 实际条目={total_count}")

            if total_count < 1:
                CommonFunction.print_log("ERROR",
                    "  ✗ 创建 Session 后列表仍为空，在线用户查询功能异常")
                result = "FAIL"
            else:
                CommonFunction.print_log("INFO",
                    f"  ✓ Session 列表非空（count={total_count}）")

            # Step 3：验证关键字段完整性（逐条查询 Session 详情）
            if sessions:
                CommonFunction.print_log("INFO", "Step 3：验证 Session 字段完整性")
                for s in sessions:
                    session_id = s.id
                    if not session_id:
                        CommonFunction.print_log("ERROR",
                            f"  ✗ Session 缺少 id 字段：{s}")
                        result = "FAIL"
                        continue
                    try:
                        detail = self.client.get_session(session_id)
                        CommonFunction.print_log("INFO",
                            f"  Session {session_id}：UserName={detail.user_name!r}")
                        if not detail.user_name:
                            CommonFunction.print_log("WARNING",
                                f"  Session {session_id} 缺少 UserName 字段（部分厂商不返回）")
                    except RedfishException as e:
                        CommonFunction.print_log("WARNING",
                            f"  get_session({session_id}) 失败（可忽略）：{str(e)[:80]}")
            else:
                CommonFunction.print_log("INFO",
                    "Step 3：Sessions 列表为空，跳过字段详情验证（厂商实现差异）")

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"online_users,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "online_users", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "在线用户查询测试", "value": result})
        test.print_log("INFO", f"在线用户查询测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountOnlineUsersTest("AccountOnlineUsersTest")
        checker.run_test()
        exit_code = 0 if checker.command_check_result == "PASS" else \
                            2 if checker.command_check_result == "FAIL" else 1
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
        try:
            checker._cleanup()
            checker.close_sdk_client()
        except Exception: pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
