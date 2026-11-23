#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update:
2026/10/16: SDK 1.3.0 适配 - 硬编码 URI 改为 SDK/ServiceRoot 动态发现（issue/8）
2026/10/26: 补齐 modern（MessageId）提交路径与响应校验（issue/9，思路源自上游
            PR #15，判定策略按评审调整）——
  1. 双路径提交：通过 @Redfish.ActionInfo 探测 SubmitTestEvent 参数形态——
     modern 模式（无 EventType 参数）用 MessageId 提交（自动从 Base Registry
     解析 NumberOfArgs=0 的消息，如 Base.1.x.Success；conf 可显式指定
     TestMessageId/TestMessageArgs）；legacy 模式保持 EventType 循环提交。
     Registry 不可发现时降级 WARNING 不 FAIL（避免引入新误报源）。
  2. 响应结构校验（对齐宁畅《订阅规则节选》3.9.8 响应字段）：submit_test_event
     成功后校验响应 Message 结构（@odata.type/MessageId/Message/Severity），
     字段缺失记 WARNING 不 FAIL（部分厂商返回 204 无 body 属合规行为）。
  3. 宁畅文档中的 EventId/EventTimestamp 为厂商私有扩展（非 DSP8010 标准参数，
     EventTimestamp 规范要求 BMC 自行生成），不做兼容。
Usage: python3 bmc/event_009_submit_test_event.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  模拟测试事件上报（正向）
  POST EventService.SubmitTestEvent（URI 由 SDK 从 EventService.Actions 动态获取）
  modern 模式用 MessageId 提交，legacy 模式用合法 EventType 循环提交。

  说明：
  - 此操作会触发 BMC 向已有订阅推送测试事件
  - 脚本仅验证发送端响应；推送是否到达接收端需接收端另行确认
  - submit_test_event() 成功时无返回，BMC 拒绝（4xx/5xx）时抛出 RedfishException

PASS 标准：
  modern 模式：MessageId 提交不抛异常（响应结构缺失记 WARNING）；
  legacy 模式：合法 EventType 的 submit_test_event() 均不抛出 RedfishException。
"""

import argparse
import datetime
import json
import logging
import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.bmc_event_test_base import BmcEventTestBase
from func.redfish_event_compat import (
    message_id_parameters,
    resolve_test_message,
)

from redfish_sdk import RedfishClient, RedfishException


# Redfish 标准合法 EventType 枚举（默认值，配置文件可覆盖）
DEFAULT_EVENT_TYPES = [
    "Alert",
    "ResourceUpdated",
    "ResourceAdded",
    "ResourceRemoved",
    "StatusChange",
]

# SubmitTestEvent 成功响应的 Message 结构字段（宁畅文档 3.9.8 / DSP8010）
# 字段缺失记 WARNING 不 FAIL：部分厂商返回 204 无 body 属合规行为
RESPONSE_MESSAGE_FIELDS = ["@odata.type", "MessageId", "Message", "Severity"]


class Event009SubmitTestEvent(BmcEventTestBase):
    CASE_KEY      = 'Event009SubmitTestEvent'
    TEST_NAME     = 'EventService 测试事件上报（正向）'
    TEST_NUM      = 'Redfish_Event_009'
    LOG_BASE_NAME = 'event_009_submit_test_event'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'

    def _load_extra_config(self, conf: dict) -> None:
        """加载 event_types / TestMessageId 配置。

        配置文件示例（conf/bmc/event/event_test.json）：
            "Event009SubmitTestEvent": {
                ...
                "EventTypes": ["Alert", "StatusChange"],
                "TestMessageId": "",        # modern 模式 MessageId，留空自动从 Base Registry 解析
                "TestMessageArgs": []       # modern 模式 MessageArgs
            }
        """
        self.event_types = conf.get("EventTypes", DEFAULT_EVENT_TYPES)
        self.test_message_id = conf.get("TestMessageId") or None
        self.test_message_args = conf.get("TestMessageArgs", [])

    def _verify_response_message(self, findings: list, label: str) -> None:
        """校验 SubmitTestEvent 成功响应的 Message 结构（宁畅文档 3.9.8 响应字段）。

        字段缺失记 WARNING 不 FAIL：部分厂商返回 204 无 body 属合规行为，
        避免引入新误报源。
        """
        try:
            resp = getattr(self.client, "_last_response", None)
            body = None
            if resp is not None:
                body = resp.json() if hasattr(resp, "json") else None
            if not isinstance(body, dict):
                self.log.info(f"  ℹ️  {label}: 响应无 JSON body（204 无 body 属合规行为）")
                return
            missing = [f for f in RESPONSE_MESSAGE_FIELDS if body.get(f) is None]
            if missing:
                self.log.warning(f"  ⚠️  {label}: 响应 Message 结构缺少字段 {missing}（记 WARNING 不 FAIL）")
                findings.append({"item": f"{label} 响应 Message 结构",
                                 "result": "WARNING",
                                 "detail": f"缺少字段：{missing}（204 无 body 属合规，字段缺失需人工关注）"})
            else:
                self.log.info(f"  ✅ {label}: 响应 Message 结构完整")
                findings.append({"item": f"{label} 响应 Message 结构",
                                 "result": "PASS",
                                 "detail": "Message 结构字段完整"})
        except Exception as e:
            # 响应校验本身失败不影响主判定
            self.log.info(f"  ℹ️  {label}: 响应结构校验跳过（{e}）")

    def _submit_kwargs(self, required: set) -> dict:
        """按 ActionInfo 的 Required 集合组装 SubmitTestEvent 参数。"""
        try:
            message_id, message_args = resolve_test_message(
                self.client, self.test_message_id, self.test_message_args
            )
        except ValueError:
            message_id, message_args = "Base.1.0.Success", []

        kwargs = {"message_id": message_id}
        if "MessageArgs" in required or not required:
            kwargs["message_args"] = message_args
        if "EventType" in required:
            kwargs["event_type"] = self.event_types[0] if self.event_types else "StatusChange"
        if "Message" in required:
            kwargs["message"] = "Redfish SubmitTestEvent test"
        if "Severity" in required:
            kwargs["severity"] = "OK"
        if "EventId" in required:
            kwargs["event_id"] = f"submit-test-{int(time.time())}"
        if "EventTimestamp" in required:
            kwargs["event_timestamp"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds")
        if "OriginOfCondition" in required:
            systems = self.client.get_systems()
            kwargs["origin_of_condition"] = (
                {"@odata.id": systems[0].odata_id} if systems else None)
        return kwargs

    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")
        self.log.info("ℹ️  此操作将向所有订阅推送测试事件")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        # v1.1.0：get_event_service().actions 获取 SubmitTestEvent 的 AllowableValues
        # 并探测参数形态（legacy=EventType / modern=MessageId）
        allowable = []
        required = None
        try:
            event_service = self.client.get_event_service()
            actions = event_service.actions or {}
            allowable = (actions.get("#EventService.SubmitTestEvent", {})
                                .get("EventType@Redfish.AllowableValues", []))
            self.log.info(f"  AllowableValues: {allowable}")
            required = message_id_parameters(self.client, event_service)
            self.log.info(f"  SubmitTestEvent 参数形态："
                          f"{'legacy（EventType）' if required is None else f'modern（Required={sorted(required)}）'}")
        except RedfishException:
            pass

        findings = []
        try:
            # ── modern 模式：MessageId 提交 ─────────────────────────────────
            if required is not None:
                try:
                    kwargs = self._submit_kwargs(required)
                    self.client.submit_test_event(**kwargs)
                    findings.append({"item": "POST SubmitTestEvent（MessageId）→ 成功",
                                     "result": "PASS",
                                     "detail": f"BMC 已接受，MessageId={kwargs['message_id']}"})
                    self.log.info(f"  ✅ MessageId={kwargs['message_id']!r} → 成功")
                    self._verify_response_message(findings, "MessageId 提交")
                except ValueError as e:
                    # Registry 不可发现且未配置 TestMessageId：降级 WARNING 不 FAIL
                    self.log.warning(f"  ⚠️  modern 模式 MessageId 解析失败：{e}（记 WARNING 不 FAIL）")
                    findings.append({"item": "POST SubmitTestEvent（MessageId）",
                                     "result": "WARNING",
                                     "detail": f"MessageId 解析失败，降级跳过：{e}"})
                except RedfishException as e:
                    findings.append({"item": "POST SubmitTestEvent（MessageId）→ 成功",
                                     "result": "FAIL",
                                     "detail": f"RedfishException: {e}"})
                    self.log.error(f"  ❌ MessageId 提交 → RedfishException: {e}")
                # modern 模式下不再走 EventType 循环（BMC 不接受 EventType 参数）

            # ── legacy 模式：EventType 循环提交 ─────────────────────────────
            else:
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
                        self._verify_response_message(findings, f"EventType={etype} 提交")
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
