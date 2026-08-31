#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Usage: python3 bmc/event_001_get_service.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  查询 EventService 资源（GET /redfish/v1/EventService）
  验证响应字段完整性，包含标准字段和 Actions（SubmitTestEvent）。

PASS 标准：
  HTTP 200，必要字段均存在，SubmitTestEvent Action 可发现。
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


class Event001GetService(BmcEventTestBase):
    CASE_KEY      = 'Event001GetService'
    TEST_NAME     = 'EventService 资源查询'
    TEST_NUM      = 'Redfish_Event_001'
    LOG_BASE_NAME = 'event_001_get_service'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'


    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            # v1.1.0：get_event_service() 返回 EventService 对象（含 actions 字段）
            event_service = self.client.get_event_service()
            findings.append({"item": "GET EventService 返回有效响应",
                             "result": "PASS" if event_service is not None else "FAIL",
                             "detail": f"type={type(event_service).__name__}"})
            self.log.info("✅ GET EventService → 有效响应")

            if event_service is not None:
                # Redfish 字段名 → v1.1.0 EventService 模型属性
                field_map = {
                    "@odata.id":                    event_service.odata_id,
                    "@odata.type":                  event_service.odata_type,
                    "ServiceEnabled":               event_service.service_enabled,
                    "EventTypesForSubscription":    event_service.event_types_for_subscription,
                    "DeliveryRetryAttempts":        event_service.delivery_retry_attempts,
                    "DeliveryRetryIntervalSeconds": event_service.delivery_retry_interval_seconds,
                    "Subscriptions":                event_service.subscriptions,
                }
                for field, val in field_map.items():
                    ok = val is not None
                    findings.append({"item": f"字段 {field} 存在",
                                     "result": "PASS" if ok else "FAIL",
                                     "detail": f"值={val!r}" if ok else "缺失"})
                    self.log.info(f"  {'✅' if ok else '❌'} {field}: {val!r}")

                actions = event_service.actions or {}
                has_submit = "#EventService.SubmitTestEvent" in actions
                findings.append({"item": "Actions 含 SubmitTestEvent",
                                 "result": "PASS" if has_submit else "FAIL",
                                 "detail": f"Actions keys={list(actions.keys())}"})
                self.log.info(f"  {'✅' if has_submit else '❌'} SubmitTestEvent Action: {has_submit}")

                self.log.info(f"  ServiceEnabled={event_service.service_enabled}, "
                              f"RetryAttempts={event_service.delivery_retry_attempts}, "
                              f"RetryInterval={event_service.delivery_retry_interval_seconds}")
        except RedfishException as e:
            findings.append({"item": "GET EventService", "result": "FAIL",
                             "detail": f"RedfishException: {e}"})
            self.log.error(f"❌ RedfishException: {e}")
        finally:
            self.client.close()

        fails = [f for f in findings if f["result"] == "FAIL"]
        final = "PASS" if not fails else "FAIL"
        self.command_check_result = final
        self.log.info(f"最终结果：{final}（{len(findings) - len(fails)}/{len(findings)} PASS）")
        self._write_result(final, findings)

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Event001GetService()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event001GetService.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
