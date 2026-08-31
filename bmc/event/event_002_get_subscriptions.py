#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Usage: python3 bmc/event_002_get_subscriptions.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  查询事件订阅集合资源（GET /redfish/v1/EventService/Subscriptions）
  验证响应结构、Members@odata.count 字段及数量一致性。

  SDK 用法：
  - 主路：client.get_subscriptions() 返回 List[Subscription]
  - 若 SDK pydantic parse 失败（如 Status 字段结构差异），记录 WARNING 并降级 get_raw 兜底
  - 厂商接口本身返回 4xx/5xx → FAIL

PASS 标准：
  返回有效集合，包含 Members 和 Members@odata.count，且数量一致。
"""

import argparse
import json
import logging
import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.bmc_event_test_base import BmcEventTestBase



from redfish_sdk import RedfishClient, RedfishException


SUB_COL_URI = "/redfish/v1/EventService/Subscriptions"

class Event002GetSubscriptions(BmcEventTestBase):
    CASE_KEY      = 'Event002GetSubscriptions'
    TEST_NAME     = 'EventService 订阅集合查询'
    TEST_NUM      = 'Redfish_Event_002'
    LOG_BASE_NAME = 'event_002_get_subscriptions'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'


    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            # 主路：SDK get_subscriptions()
            sdk_count = None
            try:
                subs = self.client.get_subscriptions()
                sdk_count = len(subs)
                findings.append({"item": "SDK get_subscriptions() 正常返回",
                                 "result": "PASS",
                                 "detail": f"返回 {sdk_count} 条 Subscription 对象"})
                self.log.info(f"  SDK get_subscriptions() → {sdk_count} 条")
            except Exception as sdk_err:
                # SDK pydantic parse 失败不是厂商问题，记 WARNING
                findings.append({"item": "SDK get_subscriptions() 正常返回",
                                 "result": "WARNING",
                                 "detail": f"SDK 内部 parse 异常（非厂商问题，建议推动 SDK 修复）：{sdk_err}"})
                self.log.warning(f"  SDK get_subscriptions() 异常（降级 get_raw 兜底）：{sdk_err}")

            # 备用/验证路：get_raw 直接拿集合结构
            col = self.client.get_raw(SUB_COL_URI)

            is_dict = isinstance(col, dict)
            findings.append({"item": f"GET {SUB_COL_URI} 返回有效响应",
                             "result": "PASS" if is_dict else "FAIL",
                             "detail": f"type={type(col).__name__}"})

            if is_dict:
                for field in ("@odata.id", "@odata.type", "Members", "Members@odata.count"):
                    present = field in col
                    findings.append({"item": f"集合字段 {field} 存在",
                                     "result": "PASS" if present else "FAIL",
                                     "detail": f"值={col.get(field)!r}" if present else "缺失"})
                    self.log.info(f"  {'✅' if present else '❌'} {field}: {col.get(field)!r}")

                members  = col.get("Members", [])
                count    = col.get("Members@odata.count", len(members))
                match    = int(count) == len(members)
                findings.append({"item": "Members@odata.count 与 Members 数量一致",
                                 "result": "PASS" if match else "FAIL",
                                 "detail": f"count={count}, len(Members)={len(members)}"})
                self.log.info(f"  {'✅' if match else '❌'} 数量一致：count={count}, Members={len(members)}")

                # 若 SDK 返回与 get_raw 数量不一致（且 SDK 未报异常），记 WARNING
                if sdk_count is not None and sdk_count != len(members):
                    findings.append({"item": "SDK 返回数量与 get_raw 一致",
                                     "result": "WARNING",
                                     "detail": f"SDK={sdk_count}, get_raw Members={len(members)}，"
                                               f"可能存在 pydantic skip（建议推动 SDK 修复）"})
                    self.log.warning(f"  SDK 数量({sdk_count}) != get_raw Members({len(members)})")

                for m in members:
                    self.log.info(f"  订阅：{m.get('@odata.id', '?')}")

        except RedfishException as e:
            findings.append({"item": "GET Subscriptions 接口", "result": "FAIL",
                             "detail": f"RedfishException: {e}"})
            self.log.error(f"RedfishException: {e}")
        finally:
            self.client.close()

        # WARNING 不算 FAIL
        fails = [f for f in findings if f["result"] == "FAIL"]
        final = "PASS" if not fails else "FAIL"
        self.command_check_result = final
        self.log.info(f"最终结果：{final}（{len(findings) - len(fails)}/{len(findings)} 非FAIL）")
        self._write_result(final, findings)

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Event002GetSubscriptions()
        obj.run_test()
        exit_code = 0 if obj.command_check_result == "PASS" else 2
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        if obj: obj.log.error(f"脚本异常：{e}\n{traceback.format_exc()}")
        else: print(f"初始化异常：{e}"); traceback.print_exc()
        exit_code = 1
    finally:
        ec_path = (obj.exit_code_path if obj else
                   os.path.join(os.getcwd(), f"result/bmc/{Event002GetSubscriptions.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
