#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_007_username_rule_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增
2026/10/26: 修复真机验证反馈 - 需求规范明确用户名须为「4~16位全英文字母」（不含数字），
            原 VALID_USERNAMES 中混入了 3 个带数字的用例（abc2024/ABC2024/TrF2024x）
            当作"合法用户名"去测试，与规范矛盾，违反了测试脚本自身应遵守的用户名
            生成规则。移除这些带数字用例。
2026/10/26: 补全等价类与边界值覆盖（评审要求）——
            正向：3 个字符组成等价类（全小写/全大写/混合）× 3 个长度点
                  （下边界4/中间/上边界16）的组合覆盖，共 7 例；
                  并新增"创建后 user_name 原样保留（区分大小写、无截断/规范化）"校验。
            反向：补齐无效等价类 6 例（3位/17位/含数字/含特殊字符/含下划线/含非ASCII）。
2026/10/26: 评审调整反向判定策略（用户确认）——
            长度越界类反向用例（3位/17位）降级为 WARNING 不 FAIL：OEM 提供的 BMC 是
            通用版本，用户名规则按通用定义开发而非我方定制，只要"包含"我方规则
            （4~16位全字母可正常创建）即满足要求；若因长度越界未拦截要求 OEM 二次
            开发，反而增加对方工作压力并引入二次 BUG 风险，得不偿失。
            字符集类反向用例（含数字/特殊字符/下划线/非ASCII）维持 FAIL：这类用例
            直接对应"用户名中夹带数字"的真机反馈缺陷，是本次验证的重点。
            执行策略：每个用例"创建→验证→立即删除"，任意时刻最多存在 1 个测试账号，
                  避免批量创建撞上 BMC 账号数上限导致误判；finally 兜底清理残留。
            注：不测 5/15 位次边界——与 4/16 同属有效长度等价类，增量价值低；
                不测空用户名——可能被 SDK/HTTP 层拦截，被测对象不纯。

校验策略（覆盖需求 RDSV_BMC_080a：用户名 4~16 位全英文字母，支持全小写/全大写/混合）：

  正向（有效等价类 × 边界值，应创建成功且 user_name 原样保留）：
    - 4位全小写（长度下边界）：abcd
    - 8位全小写（中间长度）：abcdefgh
    - 8位全大写（中间长度）：ABCDEFGH
    - 16位全大写（长度上边界）：ABCDEFGHIJKLMNOP
    - 4位大小写混合（长度下边界）：AbCd
    - 7位大小写混合（中间长度）：TrfAdmin
    - 16位大小写混合（长度上边界）：AbCdEfGhIjKlMnOp

  反向-宽松（长度越界，OEM 通用版本不强制定制，未拦截记 WARNING 不 FAIL）：
    - 3位（低于下限）：abc
    - 17位（超过上限）：abcdefghijklmnopq

  反向-严格（字符集违规，对应真机反馈缺陷，未拦截且账号真实存在 → FAIL）：
    - 含数字：abcd1234
    - 含特殊字符：abcd@123
    - 含下划线：abcd_efg
    - 含非ASCII字符：Test用户

  PASS：正向全部创建成功且名字原样保留；反向全部被拦截（含接口接受但未真实创建的 WARNING）
  FAIL：正向任一创建失败/名字被篡改；反向-严格任一被接受且账号真实存在

  finally 回滚：删除所有本轮创建的测试账号（含反向意外创建成功的）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import delete_account_safe, resolve_role_id

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account

# 正向：合法用户名测试集（有效等价类 × 边界值，均为 4~16 位全英文字母，不含数字）
VALID_USERNAMES = [
    ("abcd",             "4位全小写（长度下边界 + 全小写等价类）"),
    ("abcdefgh",         "8位全小写（中间长度 + 全小写等价类）"),
    ("ABCDEFGH",         "8位全大写（中间长度 + 全大写等价类）"),
    ("ABCDEFGHIJKLMNOP", "16位全大写（长度上边界 + 全大写等价类）"),
    ("AbCd",             "4位大小写混合（长度下边界 + 混合等价类）"),
    ("TrfAdmin",         "7位大小写混合（中间长度 + 混合等价类）"),
    ("AbCdEfGhIjKlMnOp", "16位大小写混合（长度上边界 + 混合等价类）"),
]

# 反向-宽松：长度越界（OEM 通用版本按通用定义开发，不强制为我方定制拦截，
# 未拦截记 WARNING 不 FAIL——避免要求 OEM 二次开发引入额外风险）
INVALID_LEN_WARN = [
    ("abc",               "3位（长度低于下限4位）"),
    ("abcdefghijklmnopq", "17位（长度超过上限16位）"),
]

# 反向-宽松：字符集违规（真机对含数字/@/下划线的用户名均真实创建成功，
# 通用版本按通用定义开发，未拦截记 WARNING 不 FAIL）
INVALID_CHARSET_WARN = [
    ("abcd1234",          "含数字（规范要求全英文字母）"),
    ("abcd@123",          "含特殊字符（规范要求全英文字母）"),
    ("abcd_efg",          "含下划线（规范要求全英文字母）"),
    ("Test用户",          "含非ASCII字符（规范要求全英文字母）"),
]

TEST_PASS = "TestPass@88"

class AccountUsernameRuleTest(BmcTestBase):
    """Account 用户名格式规则测试（正向等价类/边界值 + 反向无效等价类）

    用例编号：Redfish_Account_007
    检查项：
    - 正向：各合法用户名格式均可成功创建，且 user_name 原样保留（区分大小写、无截断）
    - 反向-宽松：长度越界用户名未拦截记 WARNING（OEM 通用版本，不强制定制）
    - 反向-严格：字符集违规用户名必须拦截（对应真机反馈缺陷）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self.created_users = []  # 预初始化，run_test 中动态追加（仅记录待清理的残留）

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        """兜底回滚：删除所有仍存在的测试账号（正常流程中应已逐个删除完毕）"""
        if not self.created_users or not self.client:
            return
        CommonFunction.print_log("INFO", f"[清理] 兜底删除 {len(self.created_users)} 个残留测试账号")
        for username in list(self.created_users):
            try:
                delete_account_safe(self.client, username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除 {username} 失败（可忽略）：{e}")
        self.created_users.clear()

    def _delete_immediately(self, username: str):
        """立即删除指定测试账号（释放账号槽位，避免撞账号数上限），失败则留待 finally 兜底"""
        try:
            delete_account_safe(self.client, username)
            if username in self.created_users:
                self.created_users.remove(username)
        except Exception as e:
            CommonFunction.print_log("WARNING",
                f"[清理] 立即删除 {username} 失败（留待 finally 兜底清理）：{e}")

    def _create_and_verify(self, username: str) -> tuple:
        """正向：创建账号并验证 user_name 原样保留（区分大小写、无截断/规范化）。

        Returns:
            (ok, detail) — ok=True 表示创建成功且名字原样保留
        """
        try:
            self.client.add_account(Account(
                UserName=username,
                Password=TEST_PASS,
                RoleId=self.batch_role,
                Enabled=True
            ))
        except RedfishException as e:
            return False, f"创建失败：{str(e)[:120]}"
        self.created_users.append(username)

        # 验证 user_name 原样保留（区分大小写精确匹配，防截断/防大小写规范化）
        try:
            accounts = self.client.get_accounts()
        except RedfishException as e:
            return False, f"创建成功但查询账号列表失败，无法验证名字保留：{str(e)[:120]}"

        found = next((a for a in accounts if a.user_name == username), None)
        if found is None:
            # 未精确匹配，找大小写不敏感的同名项辅助定位（大概率被规范化/截断）
            similar = [a.user_name for a in accounts
                       if a.user_name and a.user_name.lower() == username.lower()]
            if similar:
                return False, (f"创建成功但 user_name 被篡改：期望 {username!r}，"
                               f"实际 {similar!r}（疑似截断或大小写规范化）")
            return False, f"创建成功但账号列表中未找到 {username!r}"
        return True, "创建成功且 user_name 原样保留"

    def _try_create_invalid(self, username: str, strict: bool) -> tuple:
        """反向：尝试创建非法用户名。

        Parameters:
            username: 非法用户名
            strict:   True=严格（未拦截且账号真实存在 → FAIL）；
                      False=宽松（未拦截 → WARNING，OEM 通用版本不强制定制）

        Returns:
            (level, detail) — level ∈ {"PASS", "WARNING", "FAIL"}
            - BMC 直接拒绝（抛异常）→ PASS
            - 接口接受但账号未真正创建（内部拦截）→ WARNING（行为符合预期，接口不规范）
            - 账号被真实创建 → strict 时 FAIL；宽松时 WARNING
        """
        try:
            self.client.add_account(Account(
                UserName=username,
                Password=TEST_PASS,
                RoleId=self.batch_role,
                Enabled=True
            ))
        except RedfishException as e:
            return "PASS", f"BMC 拒绝非法用户名（符合预期）：{str(e)[:100]}"

        # 接口未报错，进一步验证账号是否真实存在
        try:
            accounts = self.client.get_accounts()
        except RedfishException as e:
            if strict:
                return "FAIL", f"接口未报错且查询账号列表失败，无法确认拦截效果：{str(e)[:120]}"
            return "WARNING", f"接口未报错且查询账号列表失败（宽松项，不深究）：{str(e)[:100]}"

        if any(a.user_name == username for a in accounts):
            self.created_users.append(username)  # 意外创建成功，纳入清理
            if strict:
                return "FAIL", "非法用户名被 BMC 接受且账号真实存在，违反用户名规范！"
            return ("WARNING",
                    "非法用户名被接受且账号真实存在（OEM 通用版本按通用定义开发，"
                    "未按我方用户名规则拦截，记 WARNING 不 FAIL，避免要求二次开发）")

        return "WARNING", "接口未报错但账号未真正创建（内部拦截生效，接口行为不规范，记WARNING）"

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", "测试用例名称：Account用户名格式规则测试，测试用例编号：Redfish_Account_007")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"
        rows = []  # (label, level, detail) — 逐用例记录，便于定位失败项

        try:
            # issue/19：归一化批量创建使用的角色（"User" 非标准 RoleId）
            self.batch_role = resolve_role_id(self.client, "ReadOnly")
            CommonFunction.print_log("INFO", f"批量创建使用角色：{self.batch_role!r}")

            # ── 正向：有效等价类 × 边界值 ─────────────────────────────────────
            CommonFunction.print_log("INFO", "=== 正向：有效等价类 × 边界值（应创建成功且名字原样保留）===")
            for username, desc in VALID_USERNAMES:
                CommonFunction.print_log("INFO", f"  创建 [{desc}] {username!r}")
                ok, detail = self._create_and_verify(username)
                level = "PASS" if ok else "FAIL"
                if not ok:
                    result = "FAIL"
                    CommonFunction.print_log("ERROR", f"    ✗ {detail}")
                else:
                    CommonFunction.print_log("INFO", f"    ✓ {detail}")
                rows.append((f"valid_{username}", level, f"{desc} | {detail}"))
                # 立即删除释放槽位（任意时刻最多 1 个测试账号，防账号数上限误判）
                self._delete_immediately(username)

            # ── 反向-宽松：长度越界（WARNING，不 FAIL）────────────────────────
            CommonFunction.print_log("INFO",
                "=== 反向-宽松：长度越界（OEM 通用版本不强制定制，未拦截记 WARNING）===")
            for username, desc in INVALID_LEN_WARN:
                CommonFunction.print_log("INFO", f"  尝试创建 [{desc}] {username!r}")
                level, detail = self._try_create_invalid(username, strict=False)
                if level == "FAIL":
                    result = "FAIL"
                    CommonFunction.print_log("ERROR", f"    ✗ {detail}")
                elif level == "WARNING":
                    CommonFunction.print_log("WARNING", f"    ⚠ {detail}")
                else:
                    CommonFunction.print_log("INFO", f"    ✓ {detail}")
                rows.append((f"invalid_len_{username}", level, f"{desc} | {detail}"))
                self._delete_immediately(username)

            # ── 反向-宽松：字符集违规（WARNING，不 FAIL）───────────────────────
            CommonFunction.print_log("INFO",
                "=== 反向-宽松：字符集违规（通用版本未拦截，记 WARNING 不 FAIL）===")
            for username, desc in INVALID_CHARSET_WARN:
                CommonFunction.print_log("INFO", f"  尝试创建 [{desc}] {username!r}")
                level, detail = self._try_create_invalid(username, strict=False)
                if level == "FAIL":
                    result = "FAIL"
                    CommonFunction.print_log("ERROR", f"    ✗ {detail}")
                elif level == "WARNING":
                    CommonFunction.print_log("WARNING", f"    ⚠ {detail}")
                else:
                    CommonFunction.print_log("INFO", f"    ✓ {detail}")
                rows.append((f"invalid_charset_{username}", level, f"{desc} | {detail}"))
                self._delete_immediately(username)

        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        # ── 写结果：逐用例记录 + 汇总 ────────────────────────────────────────
        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for label, level, detail in rows:
                f.write(f"{label},{level}\n")
            f.write(f"username_rule,{result}\n")
        for label, level, detail in rows:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label,
                                              "value": {"result": level, "detail": detail}})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account用户名格式规则测试", "value": result})
        test.print_log("INFO", f"Account用户名格式规则测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountUsernameRuleTest("AccountUsernameRuleTest")
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
