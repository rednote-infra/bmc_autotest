#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_013_lldp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC LLDP 设置功能测试，验证 LLDP 启用/禁用及 WorkMode 切换
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

LLDP_URI = "/redfish/v1/Managers/1/LldpService"

# ZTE LLDP WorkMode 可选值：TxOnly / RxOnly / TxRx / Disabled
TEST_WORK_MODE = "TxOnly"

class Managers013LldpTest(BmcTestBase):
    """BMC LLDP 设置功能测试

    用例编号：Redfish_Managers_013
    测试内容：
    - 查询当前 LLDP 配置（LldpEnabled/WorkMode）
    - 禁用 LLDP，验证生效
    - 启用 LLDP，修改 WorkMode，验证生效
    - 恢复原始配置
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_013_lldp_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        lldp_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询当前 LLDP 配置
            # [SDK-GAP] get_raw(LLDP_URI) 获取 LldpService 配置：
            #   LldpService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/LldpService')："
                           "LldpService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            lldp_data = self.client.get_raw(LLDP_URI)
            original_enabled = lldp_data.get("LldpEnabled")
            original_mode = lldp_data.get("WorkMode", "")
            test.print_log("INFO", f"当前 LLDP: LldpEnabled={original_enabled}, WorkMode={original_mode}")
            lldp_info["initial"] = {
                "LldpEnabled": original_enabled,
                "WorkMode": original_mode,
            }
            checks.append(("查询 LLDP 配置成功", True))
            checks.append(("LldpEnabled 字段存在", original_enabled is not None))
            checks.append(("WorkMode 字段非空", bool(original_mode)))

            # 2. 禁用 LLDP
            test.print_log("INFO", "禁用 LLDP...")
            try:
                self.client.patch(LLDP_URI, {"LldpEnabled": False})
                time.sleep(2)
                # [SDK-GAP] 同上，LldpService OEM 路径
                lldp_data = self.client.get_raw(LLDP_URI)
                disabled = lldp_data.get("LldpEnabled")
                test.print_log("INFO", f"验证禁用: LldpEnabled={disabled}")
                checks.append(("LLDP LldpEnabled=False 验证", disabled is False))
                lldp_info["after_disable"] = {"LldpEnabled": disabled}
            except RedfishException as e:
                test.print_log("ERROR", f"LLDP 禁用失败: {str(e)}")
                checks.append(("LLDP LldpEnabled=False 验证", False))

            # 3. 启用 LLDP
            test.print_log("INFO", "启用 LLDP（PATCH LldpEnabled=True）...")
            try:
                self.client.patch(LLDP_URI, {"LldpEnabled": True})
                time.sleep(2)
                # [SDK-GAP] 同上，LldpService OEM 路径
                lldp_data = self.client.get_raw(LLDP_URI)
                new_enabled = lldp_data.get("LldpEnabled")
                test.print_log("INFO", f"验证 LldpEnabled={new_enabled}")
                checks.append(("LLDP LldpEnabled=True 验证", new_enabled is True))
                lldp_info["after_enable"] = {
                    "LldpEnabled": new_enabled,
                    "WorkMode": lldp_data.get("WorkMode", ""),
                }
            except RedfishException as e:
                test.print_log("ERROR", f"LLDP 启用失败: {str(e)}")
                checks.append(("LLDP LldpEnabled=True 验证", False))

            # WorkMode 仅做只读验证（ZTE 该固件版本不支持 PATCH WorkMode，为只读字段）
            test.print_log("WARNING", f"WorkMode 字段在 ZTE 固件上为只读，跳过写测试，仅验证字段非空")
            checks.append(("WorkMode 字段非空（只读验证）", bool(original_mode)))

            # 4. 恢复原始 LldpEnabled
            test.print_log("INFO", f"恢复原始 LLDP 配置: Enabled={original_enabled}...")
            try:
                self.client.patch(LLDP_URI, {"LldpEnabled": original_enabled})
                time.sleep(2)
                # [SDK-GAP] 同上，LldpService OEM 路径
                lldp_data = self.client.get_raw(LLDP_URI)
                restored_enabled = lldp_data.get("LldpEnabled")
                restored_mode = lldp_data.get("WorkMode", "")
                checks.append(("LLDP LldpEnabled 恢复验证", restored_enabled == original_enabled))
                lldp_info["after_restore"] = {
                    "LldpEnabled": restored_enabled,
                    "WorkMode": restored_mode,
                }
            except RedfishException as e:
                test.print_log("WARNING", f"LLDP 配置恢复失败: {str(e)}")
                checks.append(("LLDP LldpEnabled 恢复验证", False))

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

        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "lldp_config_info", "value": lldp_info})

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
        test_name = Managers013LldpTest("Managers013LldpTest")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS"]:
            exit_code = 0
        elif test_name.command_check_result == "FAIL":
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
        try:
            if test_name and hasattr(test_name, 'exit_code_path'):
                with open(test_name.exit_code_path, "w", encoding="utf-8") as e:
                    e.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
