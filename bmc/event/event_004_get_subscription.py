#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update: 2026/07/02 — SDK v1.1.1 新增 get_subscription()，改用直接查询单个订阅
2026/10/26: 修复空订阅误 FAIL（issue/9，思路源自上游 PR #15 / commit 412b1c4）——
            原实现 get_subscriptions() 为空时直接 FAIL，但干净 BMC / 刚重启的 BMC
            上订阅列表为空是常态，属误报。改为自动创建临时订阅补齐前置条件：
            - 唯一 Context（配置前缀 + uuid）标记归属，避免误删业务订阅；
            - SDK subscribe() 返回值解析失败时，用 Context+Destination 精确匹配
              集合成员兜底定位（覆盖 POST 成功但解析失败的残留场景）；
            - finally 中精确清理本次创建的订阅，清理失败计入结果。
            EventTypes 判定同步适配：EventDestination v1.5.0+ 允许 EventTypes
            为 null（改用 MessageIds 等过滤字段），此时跳过非空校验。
Usage: python3 bmc/event_004_get_subscription.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  查询单个事件订阅资源（GET /redfish/v1/EventService/Subscriptions/{id}）
  验证响应字段完整性。优先查询已有订阅；为空时自动创建并清理临时订阅。

  SDK 用法（v1.1.1）：
  - client.get_subscriptions() 返回 List[Subscription]，取第一个获取 ID
  - client.get_subscription(id_or_uri) 直接查询单个订阅（v1.1.1 新增）
  - Subscription 对象属性：odata_id / odata_type / id / destination / event_types / protocol / context

PASS 标准：
  返回有效订阅资源，必要字段均存在且类型正确（v1.5.0+ 的 EventTypes 可空除外）。
"""

import argparse
import json
import logging
import os
import sys
import traceback
import uuid

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.bmc_event_test_base import BmcEventTestBase
from func.bmc_prerequisite import PrerequisiteFailure, blocked_findings
from func.redfish_event_compat import modern_event_destination, subscription_event_types

from redfish_sdk import RedfishClient, RedfishException


# SDK Subscription 对象包含的字段（属性名 → Redfish 字段名）
REQUIRED_ATTRS = [
    ("odata_id",   "@odata.id"),
    ("odata_type", "@odata.type"),
    ("id",         "Id"),
    ("destination","Destination"),
    ("event_types","EventTypes"),
    ("protocol",   "Protocol"),
    ("context",    "Context"),
]

class Event004GetSubscription(BmcEventTestBase):
    CASE_KEY      = 'Event004GetSubscription'
    TEST_NAME     = 'EventService 订阅资源查询'
    TEST_NUM      = 'Redfish_Event_004'
    LOG_BASE_NAME = 'event_004_get_subscription'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'


    def _load_extra_config(self, conf):
        self.test_sub_dest = conf.get("TestSubDestination", "https://192.0.2.99:9999/test")
        self.test_sub_context = conf.get("TestSubContext", "xhs_query_test_sub_do_not_keep")

    def _temporary_subscription_uris(self, collection_uri, context):
        """识别归属本次运行的临时订阅（含 POST 成功但 SDK 解析失败的残留）。

        按 Context + Destination 双条件精确匹配，避免误删业务订阅。
        """
        collection = self.client.get_raw(collection_uri)
        matches = []
        for member in collection.get("Members", []):
            uri = member.get("@odata.id", "")
            if not uri.startswith(collection_uri + "/"):
                continue
            resource = self.client.get_raw(uri)
            if resource.get("Context") == context and resource.get("Destination") == self.test_sub_dest:
                matches.append(uri)
        return matches

    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        temporary_context = None
        collection_uri = None
        sub_id = None
        try:
            # 先获取订阅列表，取第一个订阅的 ID
            try:
                subs = self.client.get_subscriptions()
            except Exception as exc:
                raise PrerequisiteFailure(
                    "Event subscription discovery",
                    f"Unable to list subscriptions: {type(exc).__name__}: {exc}",
                    ("Select subscription", "Query subscription", "Validate fields"),
                ) from exc
            findings.append({"item": "SDK get_subscriptions() 正常返回",
                             "result": "PASS",
                             "detail": f"共 {len(subs)} 条 Subscription 对象"})
            self.log.info(f"  SDK get_subscriptions() → {len(subs)} 条")

            if not subs:
                # 自动补齐查询用例的前置条件：创建临时订阅（唯一 Context 标记归属）
                service = self.client.get_event_service()
                subscriptions = getattr(service, "subscriptions", None)
                advertised_uri = getattr(subscriptions, "odata_id", None)
                if not isinstance(advertised_uri, str) or not advertised_uri.strip():
                    raise PrerequisiteFailure(
                        "Event subscription discovery",
                        "EventService does not advertise a Subscriptions URI",
                        ("Create temporary subscription", "Query subscription", "Validate fields"),
                    )
                collection_uri = advertised_uri.rstrip("/")
                temporary_context = f"{self.test_sub_context}_{uuid.uuid4().hex}"
                created = self.client.subscribe(
                    self.test_sub_dest, subscription_event_types(self.client), temporary_context
                )
                sub_id = getattr(created, "odata_id", None)
                if not sub_id or not sub_id.startswith(collection_uri + "/"):
                    matches = self._temporary_subscription_uris(collection_uri, temporary_context)
                    if len(matches) != 1:
                        raise ValueError("无法确定临时订阅 URI")
                    sub_id = matches[0]
                findings.append({"item": "前置：自动创建临时订阅", "result": "PASS",
                                 "detail": sub_id})
                self.log.info(f"  自动创建临时订阅：{sub_id}")
            else:
                # 已有订阅时保持原来的只读查询流程
                first_sub = subs[0]
                first_id = getattr(first_sub, "id", None)
                first_uri = getattr(first_sub, "odata_id", None)
                sub_id = first_id or first_uri
                if not sub_id:
                    raise PrerequisiteFailure(
                        "Event subscription discovery",
                        "Selected subscription has neither Id nor @odata.id",
                        ("Query subscription", "Validate fields"),
                    )
            self.log.info(f"  使用订阅 ID：{sub_id!r}")

            sub = self.client.get_subscription(sub_id)
            if temporary_context and (
                sub.context != temporary_context or sub.destination != self.test_sub_dest
            ):
                raise ValueError("查询结果与本次创建的临时订阅不匹配")
            self.log.info(f"  使用订阅 URI：{sub.odata_id!r}")

            findings.append({"item": f"GET Subscription 返回有效对象",
                             "result": "PASS",
                             "detail": f"type={type(sub).__name__}, odata_id={sub.odata_id!r}"})
            self.log.info(f"✅ Subscription 对象获取成功")

            # 逐字段验证（SDK 属性 → Redfish 字段名对照）
            for attr, redfish_field in REQUIRED_ATTRS:
                val = getattr(sub, attr, None)
                # EventDestination v1.5.0+ 允许 EventTypes 为 null（改用 MessageIds 等过滤字段）
                if attr == "event_types" and val is None and modern_event_destination(sub.odata_type):
                    continue
                present = val is not None
                findings.append({"item": f"字段 {redfish_field} 存在",
                                 "result": "PASS" if present else "FAIL",
                                 "detail": f"值={val!r}" if present else "缺失（SDK 属性为 None）"})
                self.log.info(f"  {'✅' if present else '❌'} {redfish_field} ({attr}): {val!r}")

            # EventTypes 应为非空列表（v1.5.0+ 允许为 null，跳过校验）
            et = sub.event_types
            if et is not None or not modern_event_destination(sub.odata_type):
                ok_et = isinstance(et, list) and len(et) > 0
                findings.append({"item": "EventTypes 为非空列表",
                                 "result": "PASS" if ok_et else "FAIL",
                                 "detail": f"EventTypes={et!r}"})
                self.log.info(f"  {'✅' if ok_et else '❌'} EventTypes={et!r}")

        except PrerequisiteFailure as failure:
            findings = blocked_findings(failure)
            self.log.error(
                f"BLOCKED: {failure.prerequisite}: {failure.reason}"
            )
        except RedfishException as e:
            findings.append({"item": "GET Subscription", "result": "FAIL",
                             "detail": f"RedfishException: {e}"})
            self.log.error(f"❌ RedfishException: {e}")
        except Exception as e:
            findings.append({"item": "GET Subscription", "result": "FAIL", "detail": str(e)})
            self.log.error(f"❌ {e}")
        finally:
            if temporary_context:
                try:
                    # 即使 POST 已创建资源但 SDK 解析失败，也只清理归属本次的订阅
                    for uri in self._temporary_subscription_uris(collection_uri, temporary_context):
                        self.client.delete_subscription(uri)
                    remaining = self._temporary_subscription_uris(collection_uri, temporary_context)
                    if remaining:
                        raise ValueError(f"临时订阅清理后仍存在：{remaining}")
                    findings.append({"item": "清理本次临时订阅", "result": "PASS",
                                     "detail": "本次临时订阅已清理"})
                    self.log.info("  本次临时订阅已清理")
                except Exception as e:
                    findings.append({"item": "清理本次临时订阅", "result": "FAIL", "detail": str(e)})
                    self.log.error(f"❌ 临时订阅清理失败：{e}")
            self.client.close()

        fails = [f for f in findings if f["result"] == "FAIL"]
        blocked = [f for f in findings if f["result"] == "BLOCKED"]
        final = "BLOCKED" if blocked else "PASS" if not fails else "FAIL"
        self.command_check_result = final
        self.log.info(f"最终结果：{final}（{len(findings) - len(fails)}/{len(findings)} PASS）")
        self._write_result(final, findings)

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Event004GetSubscription()
        obj.run_test()
        exit_code = {
            "PASS": 0,
            "FAIL": 2,
            "BLOCKED": 4,
        }.get(obj.command_check_result, 1)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        if obj: obj.log.error(f"脚本异常：{e}\n{traceback.format_exc()}")
        else: print(f"初始化异常：{e}"); traceback.print_exc()
        exit_code = 1
    finally:
        ec_path = (obj.exit_code_path if obj else
                   os.path.join(os.getcwd(), f"result/bmc/{Event004GetSubscription.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
