#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_020_virtual_media_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC 虚拟媒体资源检查，遍历 VirtualMedia Members 并校验 MediaTypes/ConnectedVia/Inserted
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

VIRTUAL_MEDIA_URI = "/redfish/v1/Managers/1/VirtualMedia"

class Managers020VirtualMediaCheck(BmcTestBase):
    """BMC 虚拟媒体资源检查

    用例编号：Redfish_Managers_020
    测试内容：
    - GET /redfish/v1/Managers/1/VirtualMedia 集合接口可达
    - Members 非空
    - 每个成员 MediaTypes 存在（list）
    - 每个成员 ConnectedVia 字段存在
    - 每个成员 Inserted 存在（bool）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_020_virtual_media_check.json"),
        )
    def _get_member_uri(self, member: dict) -> str:
        """从 member dict 中提取 URI（@odata.id 或 uri 字段）"""
        uri = member.get("@odata.id")
        if uri:
            return uri
        uri = member.get("uri")
        if uri:
            return uri
        return ""

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        media_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. GET VirtualMedia 集合
            test.print_log("INFO", "=== 步骤1: 查询 VirtualMedia 集合 ===")
            # [SDK-GAP] get_raw(VIRTUAL_MEDIA_URI) 获取 VirtualMedia 集合：
            #   VirtualMedia 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/VirtualMedia')："
                           "VirtualMedia 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            vm_collection = self.client.get_raw(VIRTUAL_MEDIA_URI)
            test.print_log("INFO", "VirtualMedia 集合接口查询成功")
            checks.append(("VirtualMedia 集合接口查询成功", True))

            # 2. Members 非空
            members = vm_collection.get("Members", [])
            members_ok = isinstance(members, list) and len(members) > 0
            test.print_log("INFO",
                f"Members: {'✓ 非空（{len(members)} 个）' if members_ok else '✗ 为空或类型不对'}")
            checks.append(("Members 非空", members_ok))

            # 3-5. 遍历每个成员详情
            member_details = []
            if members_ok:
                for idx, member in enumerate(members):
                    member_uri = self._get_member_uri(member)
                    if not member_uri:
                        test.print_log("WARNING", f"Member {idx}: 无法提取 URI，跳过")
                        continue

                    test.print_log("INFO", f"=== 步骤2.{idx+1}: 查询 Member[{idx}] 详情: {member_uri} ===")
                    try:
                        member_data = self.client.get_raw(member_uri)  # [SDK-GAP] 同上，VirtualMedia 成员 OEM 路径
                    except Exception as e:
                        test.print_log("WARNING", f"Member[{idx}] 查询失败: {str(e)}")
                        continue

                    member_id = member_data.get("Id", f"member_{idx}")

                    # MediaTypes 存在（list）
                    media_types = member_data.get("MediaTypes")
                    media_types_ok = isinstance(media_types, list)

                    # ConnectedVia 字段存在
                    connected_via = member_data.get("ConnectedVia")
                    connected_via_ok = connected_via is not None

                    # Inserted 存在（bool）
                    inserted = member_data.get("Inserted")
                    inserted_ok = isinstance(inserted, bool)

                    test.print_log("INFO",
                        f"Member[{idx}] Id={member_id}: "
                        f"MediaTypes={'✓' if media_types_ok else '✗'}, "
                        f"ConnectedVia={'✓' if connected_via_ok else '✗'}({connected_via!r}), "
                        f"Inserted={'✓' if inserted_ok else '✗'}({inserted!r})")

                    checks.append((f"Member[{idx}]({member_id}) MediaTypes 存在", media_types_ok))
                    checks.append((f"Member[{idx}]({member_id}) ConnectedVia 存在", connected_via_ok))
                    checks.append((f"Member[{idx}]({member_id}) Inserted 存在（bool）", inserted_ok))

                    member_details.append({
                        "Id": member_id,
                        "MediaTypes": media_types,
                        "ConnectedVia": connected_via,
                        "Inserted": inserted,
                    })
            else:
                test.print_log("ERROR", "Members 为空，跳过详情查询")

            # 记录详细信息
            media_info["member_count"] = len(members) if isinstance(members, list) else 0
            media_info["members"] = member_details

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

        # 结果写入
        for md in media_info.get("members", []):
            for k in ["Id", "MediaTypes", "ConnectedVia", "Inserted"]:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": f"Member[{md.get('Id', '?')}].{k}", "value": md.get(k)})
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NAME, "value": final})
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Managers020VirtualMediaCheck("Managers020VirtualMediaCheck")
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
