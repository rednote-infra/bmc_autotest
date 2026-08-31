#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Usage: python3 bmc/event_009_submit_test_event.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  模拟测试事件上报（正向）
  POST /redfish/v1/EventService/Actions/EventService.SubmitTestEvent
  使用合法的 EventType，验证 BMC 正常响应（不抛出异常）。

  说明：
  - 此操作会触发 BMC 向已有订阅（含叶尘同学的 10.27.97.201:8444）推送测试事件
  - 脚本仅验证发送端响应；推送是否到达接收端需接收端另行确认
  - submit_test_event() 成功时无返回，BMC 拒绝（4xx/5xx）时抛出 RedfishException

PASS 标准：
  合法 EventType 的 submit_test_event() 均不抛出 RedfishException（即 BMC 接受请求）。
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


# Redfish 标准合法 EventType 枚举（默认值，配置文件可覆盖）
DEFAULT_EVENT_TYPES = [
    "Alert",
    "ResourceUpdated",
    "ResourceAdded",
    "ResourceRemoved",
    "StatusChange",
]


class Event009SubmitTestEvent(BmcEventTestBase):
    CASE_KEY      = 'Event009SubmitTestEvent'
    TEST_NAME     = 'EventService 测试事件上报（正向）'
    TEST_NUM      = 'Redfish_Event_009'
    LOG_BASE_NAME = 'event_009_submit_test_event'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'

    def _load_extra_config(self, conf: dict) -> None:
        """加载 event_types 配置，若未配置则使用 Redfish 标准枚举默认值。

        配置文件示例（conf/bmc/event/event_test.json）：
            "Event009SubmitTestEvent": {
                ...
                "EventTypes": ["Alert", "StatusChange"]
            }
        """
        self.event_types = conf.get("EventTypes", DEFAULT_EVENT_TYPES)

    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")
        self.log.info("ℹ️  此操作将向所有订阅（含叶尘同学的 10.27.97.201:8444）推送测试事件")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        # v1.1.0：get_event_service().actions 获取 SubmitTestEvent 的 AllowableValues
        allowable = []
        try:
            event_service = self.client.get_event_service()
            actions = event_service.actions or {}
            allowable = (actions.get("#EventService.SubmitTestEvent", {})
                                .get("EventType@Redfish.AllowableValues", []))
            self.log.info(f"  AllowableValues: {allowable}")
        except RedfishException:
            pass

        findings = []
        try:
            for etype in self.event_types:
                # 若 BMC 声明了 AllowableValues 且不含该 etype，跳过
                if allowable and etype not in allowable:
                    self.log.info(f"  ℹ️  EventType={etype!r} 不在 AllowableValues，跳过")
                    findings.append({"item": f"POST SubmitTestEvent EventType={etype}",
                                     "result": "PASS",
                                     "detail": f"不在 AllowableValues，跳过（{allowable}）"})
                    continue

                try:
                    self.client.submit_test_event(etype)
                    # submit_test_event 未抛异常即表示 BMC 接受请求
                    findings.append({"item": f"POST SubmitTestEvent EventType={etype} → 成功",
                                     "result": "PASS",
                                     "detail": "BMC 已接受（submit_test_event 未抛异常）"})
                    self.log.info(f"  ✅ EventType={etype!r} → 成功")
                except RedfishException as e:
                    findings.append({"item": f"POST SubmitTestEvent EventType={etype} → 成功",
                                     "result": "FAIL",
                                     "detail": f"RedfishException: {e}"})
                    self.log.error(f"  ❌ EventType={etype!r} → RedfishException: {e}")

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
        obj = Event009SubmitTestEvent()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event009SubmitTestEvent.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
