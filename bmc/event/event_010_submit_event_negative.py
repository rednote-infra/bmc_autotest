#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Usage: python3 bmc/event_010_submit_event_negative.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  模拟测试事件上报（反向）
  POST /redfish/v1/EventService/Actions/EventService.SubmitTestEvent
  使用非法参数，验证 BMC 正确拒绝请求（client.post() 抛出 RedfishException）。

  反向场景：
  1. 非法 EventType 值（"InvalidEventType_XHS_TEST"）→ 期望 RedfishException（4xx）
  2. 缺少必要字段（空 body {}）                      → 期望 RedfishException（4xx）
  3. 非法字段名（"MessageId":"xxx"）                  → 期望 RedfishException（4xx）

PASS 标准：
  所有反向场景均触发 RedfishException（即 BMC 返回 4xx 错误）。
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


SUBMIT_URI = "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"

NEGATIVE_CASES = [
    {
        "desc": "非法 EventType 值",
        "body": {"EventType": "InvalidEventType_XHS_TEST"},
    },
    {
        "desc": "缺少必要字段（空 body）",
        "body": {},
    },
    {
        "desc": "非法字段名（MessageId）",
        "body": {"MessageId": "xhs_test_invalid_field"},
    },
]

class Event010SubmitEventNegative(BmcEventTestBase):
    CASE_KEY      = 'Event010SubmitEventNegative'
    TEST_NAME     = 'EventService 测试事件上报（反向）'
    TEST_NUM      = 'Redfish_Event_010'
    LOG_BASE_NAME = 'event_010_submit_event_negative'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'


    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            for case in NEGATIVE_CASES:
                self.log.info(f"  场景：{case['desc']}")
                try:
                    resp = self.client.post(SUBMIT_URI, case["body"])
                    # 未抛异常 = BMC 接受了非法请求，这是 FAIL
                    findings.append({"item": f"[反向] {case['desc']} → 应拒绝（抛 RedfishException）",
                                     "result": "FAIL",
                                     "detail": f"BMC 未拒绝，返回 {resp!r}"})
                    self.log.error(f"  ❌ 未拒绝，返回 {resp!r}")
                except RedfishException as e:
                    # 抛出异常 = BMC 正确拒绝
                    findings.append({"item": f"[反向] {case['desc']} → 正确拒绝（RedfishException）",
                                     "result": "PASS",
                                     "detail": f"RedfishException: {e}"})
                    self.log.info(f"  ✅ 正确拒绝：{e}")

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
        obj = Event010SubmitEventNegative()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event010SubmitEventNegative.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
