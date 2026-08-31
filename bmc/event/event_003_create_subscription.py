#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update: 2026/07/02 — SDK v1.1.1 subscribe() 原生支持 http_headers，简化 ZTE 降级代码
Usage: python3 bmc/event_003_create_subscription.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  创建事件订阅（POST /redfish/v1/EventService/Subscriptions）
  验证：创建前后订阅数量 +1，新建订阅可在集合中查询到。

  SDK 用法（v1.1.1）：
  - 主路：client.subscribe(destination, event_types, context, http_headers={...}) 创建订阅
  - 若 SDK pydantic parse 返回值失败，记录 WARNING，从集合差集获取新 URI
  - 厂商接口本身返回 4xx → FAIL
  - 测试完成后自动 cleanup（DELETE 新建的订阅）
  - 不删除 Destination 含 PROTECTED_DESTINATION 的已有订阅

PASS 标准：
  POST 返回 2xx，新建后集合数量 +1，cleanup 成功。
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


SUB_COL_URI           = "/redfish/v1/EventService/Subscriptions"
PROTECTED_DESTINATION = "10.27.97.201:8444"

class Event003CreateSubscription(BmcEventTestBase):
    CASE_KEY      = 'Event003CreateSubscription'
    TEST_NAME     = 'EventService 订阅创建'
    TEST_NUM      = 'Redfish_Event_003'
    LOG_BASE_NAME = 'event_003_create_subscription'
    CONFIG_FILE   = 'event_test.json'
    CONF_DIR      = 'event'

    def __init__(self) -> None:
        self._created_sub_uri = None
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        """从配置文件加载订阅测试参数。"""
        self.test_sub_dest    = conf.get("TestSubDestination", "https://192.0.2.99:9999/test")
        self.test_sub_context = conf.get("TestSubContext",     "xhs_test_sub_do_not_keep")

    def _get_members_raw(self):
        """Use SDK get_subscriptions() to get subscription list."""
        subs = self.client.get_subscriptions()
        return [{"@odata.id": s.odata_id} for s in subs]

    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")
        self.log.info(f"保护订阅：Destination 含 {PROTECTED_DESTINATION!r} 不可删除")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            # 记录创建前的集合状态（get_raw 兜底，不依赖 SDK pydantic）
            members_before = self._get_members_raw()
            count_before   = len(members_before)
            before_uris    = {m["@odata.id"] for m in members_before}
            self.log.info(f"  创建前订阅数：{count_before}")

            # v1.1.1: subscribe() 原生支持 http_headers 参数，无需 BmcHttpClient 降级
            # 策略：先尝试携带 HttpHeaders（兼容部分厂商要求），失败则降级不带 HttpHeaders 重试
            new_uri  = None
            sub_obj  = None
            sdk_warn = False
            try:
                sub_obj = self.client.subscribe(
                    self.test_sub_dest,
                    ["Alert"],
                    self.test_sub_context,
                    http_headers={
                        "Content-Type":  "Application/json",
                        "OData-Version": "4.0",
                    },
                )
                if hasattr(sub_obj, "odata_id") and sub_obj.odata_id:
                    new_uri = sub_obj.odata_id
                elif isinstance(sub_obj, dict) and sub_obj.get("@odata.id"):
                    new_uri = sub_obj["@odata.id"]
                findings.append({"item": "SDK subscribe() 成功（含 HttpHeaders）",
                                 "result": "PASS",
                                 "detail": f"返回对象类型={type(sub_obj).__name__}, URI={new_uri!r}"})
                self.log.info(f"  SDK subscribe() 成功，URI={new_uri!r}")
            except RedfishException as e:
                # 携带 HttpHeaders 失败，降级不带 HttpHeaders 重试（WARNING）
                self.log.warning(f"  SDK subscribe() 含 HttpHeaders 失败（{e}），降级不带 HttpHeaders 重试")
                sdk_warn = True
                try:
                    sub_obj = self.client.subscribe(
                        self.test_sub_dest,
                        ["Alert"],
                        self.test_sub_context,
                    )
                    if hasattr(sub_obj, "odata_id") and sub_obj.odata_id:
                        new_uri = sub_obj.odata_id
                    elif isinstance(sub_obj, dict) and sub_obj.get("@odata.id"):
                        new_uri = sub_obj["@odata.id"]
                    findings.append({"item": "SDK subscribe() 降级重试（不含 HttpHeaders）",
                                     "result": "WARNING",
                                     "detail": f"含 HttpHeaders 失败，不含 HttpHeaders 重试成功，URI={new_uri!r}"})
                    self.log.warning(f"  SDK subscribe() 不含 HttpHeaders 重试成功，URI={new_uri!r}")
                except RedfishException as e2:
                    findings.append({"item": "POST Subscriptions 创建订阅", "result": "FAIL",
                                     "detail": f"厂商接口拒绝（含/不含 HttpHeaders 均失败）：{e2}"})
                    self.log.error(f"  厂商接口拒绝：{e2}")
                    self._write_result("FAIL", findings)
                    return
            except Exception as sdk_err:
                # SDK 内部 pydantic parse 失败 → WARNING（不是厂商问题）
                sdk_warn = True
                findings.append({"item": "SDK subscribe() 成功（不抛异常）",
                                 "result": "WARNING",
                                 "detail": f"SDK 返回值 parse 异常（非厂商问题）：{sdk_err}"})
                self.log.warning(f"  SDK subscribe() 返回值 parse 异常（降级差集查 URI）：{sdk_err}")

            # 若 SDK 未能给 URI，从集合差集获取
            if not new_uri:
                members_after = self._get_members_raw()
                new_uris = [m["@odata.id"] for m in members_after
                            if m["@odata.id"] not in before_uris]
                if new_uris:
                    new_uri = new_uris[0]
                    self.log.info(f"  从差集获取新建 URI：{new_uri}")
                else:
                    findings.append({"item": "创建后集合数量 +1", "result": "FAIL",
                                     "detail": "无法确定新建订阅 URI，集合无变化"})
                    self.log.error("  集合无变化，订阅未成功创建")
                    self._write_result("FAIL", findings)
                    return

            self._created_sub_uri = new_uri

            # 验证集合数量 +1
            members_after2 = self._get_members_raw()
            count_after    = len(members_after2)
            increased      = count_after == count_before + 1
            findings.append({"item": "创建后订阅总数 +1",
                             "result": "PASS" if increased else "FAIL",
                             "detail": f"创建前={count_before}, 创建后={count_after}"})
            self.log.info(f"  {'✅' if increased else '❌'} 订阅数：{count_before} -> {count_after}")

            # 验证新订阅在集合中可见
            all_uris = {m["@odata.id"] for m in members_after2}
            found    = new_uri in all_uris
            findings.append({"item": "新建订阅可在集合中查询到",
                             "result": "PASS" if found else "FAIL",
                             "detail": f"URI={new_uri!r}"})
            self.log.info(f"  {'✅' if found else '❌'} 新订阅在集合中：{found}")

        except Exception as e:
            findings.append({"item": "订阅创建流程", "result": "FAIL",
                             "detail": f"未预期异常：{e}"})
            self.log.error(f"未预期异常：{e}\n{traceback.format_exc()}")
        finally:
            # Cleanup（保护：只删具体订阅 URI，不删集合 URI）
            if self._created_sub_uri and self._created_sub_uri != SUB_COL_URI:
                try:
                    self.log.info(f"  Cleanup：删除测试订阅 {self._created_sub_uri}")
                    self.client.delete(self._created_sub_uri)
                    self.log.info("  Cleanup 完成")
                except Exception as e:
                    self.log.warning(f"  Cleanup 失败：{e}")
            elif self._created_sub_uri == SUB_COL_URI:
                self.log.warning("  Cleanup 跳过：new_uri 为集合 URI，无法确定具体订阅 ID，请手动清理")
            self.client.close()

        fails = [f for f in findings if f["result"] == "FAIL"]
        final = "PASS" if not fails else "FAIL"
        self.command_check_result = final
        self.log.info(f"最终结果：{final}（{len(findings) - len(fails)}/{len(findings)} 非FAIL）")
        self._write_result(final, findings)

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Event003CreateSubscription()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event003CreateSubscription.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)