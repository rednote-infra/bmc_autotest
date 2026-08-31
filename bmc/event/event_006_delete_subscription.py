#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update: 2026/07/02 — SDK v1.1.1 subscribe() 原生支持 http_headers，简化 ZTE 降级代码
Usage: python3 bmc/event_006_delete_subscription.py -i <bmc_ip> -u <username> -p <password>

测试内容：
  删除事件订阅资源（DELETE /redfish/v1/EventService/Subscriptions/{id}）
  测试流程：先 subscribe() 创建测试订阅 → delete_subscription() 删除 → 确认集合数量减少。

  SDK 用法（v1.1.1）：
  - 前置创建：client.subscribe()（支持 http_headers 参数，原生兼容部分厂商）
  - 主路删除：client.delete_subscription(id_or_uri)（v1.1.1 支持完整 URI）
  - 厂商接口 4xx → FAIL；SDK parse 失败 → WARNING
  - 不删除 Destination 含 PROTECTED_DESTINATION 的已有订阅

PASS 标准：
  delete_subscription() 不抛 RedfishException，删除后集合数量恢复。
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

class Event006DeleteSubscription(BmcEventTestBase):
    CASE_KEY      = 'Event006DeleteSubscription'
    TEST_NAME     = 'EventService 订阅删除'
    TEST_NUM      = 'Redfish_Event_006'
    LOG_BASE_NAME = 'event_006_delete_subscription'
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

    def _get_sub_id_from_uri(self, uri: str) -> str:
        return uri.rstrip("/").split("/")[-1]

    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")
        self.log.info(f"保护订阅：Destination 含 {PROTECTED_DESTINATION!r} 不可删除")

        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        findings = []
        try:
            # 记录基准状态
            members_before = self._get_members_raw()
            count_before   = len(members_before)
            before_uris    = {m["@odata.id"] for m in members_before}
            self.log.info(f"  创建前订阅数：{count_before}")

            # 前置：SDK subscribe() 创建测试订阅（v1.1.1 原生支持 http_headers）
            new_uri = None
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
                findings.append({"item": "前置：SDK subscribe() 创建测试订阅（含 HttpHeaders）",
                                 "result": "PASS",
                                 "detail": f"URI={new_uri!r}"})
                self.log.info(f"  前置 subscribe() 成功，URI={new_uri!r}")
            except RedfishException as e:
                # 携带 HttpHeaders 失败，降级不带 HttpHeaders 重试（WARNING）
                self.log.warning(f"  SDK subscribe() 含 HttpHeaders 失败（{e}），降级不带 HttpHeaders 重试")
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
                    findings.append({"item": "前置：SDK subscribe() 降级重试（不含 HttpHeaders）",
                                     "result": "WARNING",
                                     "detail": f"含 HttpHeaders 失败，不含 HttpHeaders 重试成功，URI={new_uri!r}"})
                    self.log.warning(f"  SDK subscribe() 不含 HttpHeaders 重试成功，URI={new_uri!r}")
                except RedfishException as e2:
                    findings.append({"item": "前置：创建测试订阅", "result": "FAIL",
                                     "detail": f"厂商拒绝（含/不含 HttpHeaders 均失败）：{e2}"})
                    self.log.error(f"  前置失败：{e2}")
                    self._write_result("FAIL", findings)
                    return
            except Exception as sdk_err:
                findings.append({"item": "前置：SDK subscribe() 创建测试订阅",
                                 "result": "WARNING",
                                 "detail": f"SDK parse 异常（非厂商问题）：{sdk_err}"})
                self.log.warning(f"  SDK subscribe() parse 异常，降级差集获取 URI：{sdk_err}")

            # 若 SDK 未给 URI，从差集获取
            if not new_uri:
                members_after = self._get_members_raw()
                new_uris = [m["@odata.id"] for m in members_after
                            if m["@odata.id"] not in before_uris]
                if new_uris:
                    new_uri = new_uris[0]
                    self.log.info(f"  从差集获取新建 URI：{new_uri}")
                else:
                    findings.append({"item": "前置：获取新建订阅 URI", "result": "FAIL",
                                     "detail": "集合无变化，订阅未成功创建"})
                    self.log.error("  集合无变化，无法继续删除测试")
                    self._write_result("FAIL", findings)
                    return

            self._created_sub_uri = new_uri
            sub_id = self._get_sub_id_from_uri(new_uri)

            # 主测试：SDK delete_subscription(id_or_uri)（v1.1.1 支持完整 URI）
            self.client.delete_subscription(sub_id)
            self._created_sub_uri = None  # 标记已删除
            findings.append({"item": f"SDK delete_subscription({sub_id!r}) 不抛异常",
                             "result": "PASS",
                             "detail": "delete_subscription() 执行成功"})
            self.log.info(f"  delete_subscription({sub_id!r}) 成功")

            # 验证集合数量恢复
            members_final = self._get_members_raw()
            count_final   = len(members_final)
            restored      = count_final == count_before
            findings.append({"item": "删除后订阅总数恢复",
                             "result": "PASS" if restored else "FAIL",
                             "detail": f"删除前={count_before}, 删除后={count_final}"})
            self.log.info(f"  {'✅' if restored else '❌'} 订阅数：{count_before+1} -> {count_final}（期望 {count_before}）")

            # 验证 URI 不再在集合中
            final_uris   = {m["@odata.id"] for m in members_final}
            still_exists = new_uri in final_uris
            findings.append({"item": "已删除订阅不再出现在集合中",
                             "result": "PASS" if not still_exists else "FAIL",
                             "detail": f"URI={new_uri!r}"})
            self.log.info(f"  {'✅' if not still_exists else '❌'} 订阅已从集合删除：{not still_exists}")

        except RedfishException as e:
            findings.append({"item": "订阅删除流程", "result": "FAIL",
                             "detail": f"RedfishException: {e}"})
            self.log.error(f"RedfishException: {e}")
        except Exception as e:
            findings.append({"item": "订阅删除流程", "result": "FAIL",
                             "detail": f"未预期异常：{e}"})
            self.log.error(f"未预期异常：{e}\n{traceback.format_exc()}")
        finally:
            # Cleanup：若删除未成功，强制清理
            if self._created_sub_uri:
                try:
                    sub_id = self._get_sub_id_from_uri(self._created_sub_uri)
                    self.log.info(f"  Cleanup：强制删除残留订阅 id={sub_id}")
                    self.client.delete_subscription(sub_id)
                    self.log.info("  Cleanup 完成")
                except Exception as e:
                    self.log.warning(f"  Cleanup 失败：{e}")
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
        obj = Event006DeleteSubscription()
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
                   os.path.join(os.getcwd(), f"result/bmc/{Event006DeleteSubscription.LOG_BASE_NAME}/exit_code"))
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)