#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update: 2026/07/02 — SDK v1.1.1 新增 get_subscription()，改用直接查询单个订阅
Usage: python3 bmc/event_004_get_subscription.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  查询单个事件订阅资源（GET /redfish/v1/EventService/Subscriptions/{id}）
  验证响应字段完整性。使用当前已存在的第一个订阅（无需创建）。

  SDK 用法（v1.1.1）：
  - client.get_subscriptions() 返回 List[Subscription]，取第一个获取 ID
  - client.get_subscription(id_or_uri) 直接查询单个订阅（v1.1.1 新增）
  - Subscription 对象属性：odata_id / odata_type / id / destination / event_types / protocol / context

PASS 标准：
  返回有效订阅资源，必要字段均存在且类型正确。
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


    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            # 先获取订阅列表，取第一个订阅的 ID
            subs = self.client.get_subscriptions()
            findings.append({"item": "SDK get_subscriptions() 正常返回",
                             "result": "PASS",
                             "detail": f"共 {len(subs)} 条 Subscription 对象"})
            self.log.info(f"  SDK get_subscriptions() → {len(subs)} 条")

            if not subs:
                findings.append({"item": "前置：当前存在至少一个订阅", "result": "FAIL",
                                 "detail": "get_subscriptions() 返回空列表，无法执行查询测试"})
                self.log.error("❌ 无任何订阅，无法执行测试")
                self._write_result("FAIL", findings)
                return

            # 取第一个订阅的 ID，用 get_subscription() 直接查询（v1.1.1 新增）
            first_sub = subs[0]
            sub_id = first_sub.id if first_sub.id else first_sub.odata_id
            self.log.info(f"  使用订阅 ID：{sub_id!r}")

            sub = self.client.get_subscription(sub_id)
            self.log.info(f"  使用订阅 URI：{sub.odata_id!r}")

            findings.append({"item": f"GET Subscription 返回有效对象",
                             "result": "PASS",
                             "detail": f"type={type(sub).__name__}, odata_id={sub.odata_id!r}"})
            self.log.info(f"✅ Subscription 对象获取成功")

            # 逐字段验证（SDK 属性 → Redfish 字段名对照）
            for attr, redfish_field in REQUIRED_ATTRS:
                val = getattr(sub, attr, None)
                present = val is not None
                findings.append({"item": f"字段 {redfish_field} 存在",
                                 "result": "PASS" if present else "FAIL",
                                 "detail": f"值={val!r}" if present else "缺失（SDK 属性为 None）"})
                self.log.info(f"  {'✅' if present else '❌'} {redfish_field} ({attr}): {val!r}")

            # EventTypes 应为非空列表
            et = sub.event_types
            ok_et = isinstance(et, list) and len(et) > 0
            findings.append({"item": "EventTypes 为非空列表",
                             "result": "PASS" if ok_et else "FAIL",
                             "detail": f"EventTypes={et!r}"})
            self.log.info(f"  {'✅' if ok_et else '❌'} EventTypes={et!r}")

        except RedfishException as e:
            findings.append({"item": "GET Subscription", "result": "FAIL",
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
        obj = Event004GetSubscription()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event004GetSubscription.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)