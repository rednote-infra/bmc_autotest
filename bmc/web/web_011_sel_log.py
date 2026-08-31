#!/bin/python
"""
BMC Web SEL日志查询（Web_011）
RDSV_BMC_137 - SEL日志条目数及字段完整性

查询策略（优先级递减）：
  1. 配置 SelUri 直接访问（默认 /redfish/v1/Managers/1/LogServices/SEL/Entries）
  2. 若 404，则依次枚举以下 Redfish 标准根路径下的 LogServices，
     查找 Id/Name 含 'sel' 的条目，取其 Entries href：
       a. 配置 LogServicesUri（默认 /redfish/v1/Managers/1/LogServices）
       b. /redfish/v1/Systems/1/LogServices
       c. /redfish/v1/Chassis/1/LogServices
  3. 若均无，记 FAIL

Usage: python3 -m bmc.web_011_sel_log -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebSelLog(BmcWebTestBase):
    CASE        = 'WebSelLog'
    CONFIG_FILE = 'web_011_sel_log.json'

    def _load_extra_config(self, conf: dict) -> None:
        self.conf = conf

    def _search_sel_in_log_services(self, client: 'BmcWebClient', log_svc_uri: str) -> str | None:
        """在单个 LogServices 根路径下搜索 SEL 条目，返回 Entries URI 或 None。"""
        code, body = client.get(log_svc_uri)
        if code != 200 or not isinstance(body, dict):
            return None

        members = body.get('Members', [])
        for m in members:
            href = m.get('@odata.id', '')
            if not href:
                continue
            # 先凭路径快速判断
            if 'sel' in href.lower():
                entries_uri = href.rstrip('/') + '/Entries'
                CommonFunction.print_log('INFO', f'[SEL发现] 路径匹配 {href}，Entries={entries_uri}')
                return entries_uri
            # 否则 GET 进去看 Id/Name
            sc, sb = client.get(href)
            if sc == 200 and isinstance(sb, dict):
                svc_id   = str(sb.get('Id', '')).lower()
                svc_name = str(sb.get('Name', '')).lower()
                if 'sel' in svc_id or 'sel' in svc_name:
                    entries_ref = sb.get('Entries', {})
                    if isinstance(entries_ref, dict):
                        entries_uri = entries_ref.get('@odata.id', '')
                    else:
                        entries_uri = href.rstrip('/') + '/Entries'
                    CommonFunction.print_log('INFO', f'[SEL发现] Id/Name 匹配 {href}，Entries={entries_uri}')
                    return entries_uri if entries_uri else None
        return None

    def _discover_sel_entries_uri(self, client: 'BmcWebClient') -> str | None:
        """按优先级枚举多个 Redfish 标准 LogServices 根路径，查找 SEL 的 Entries URI。
        依次搜索：配置路径 → Systems → Chassis（均为规范允许的 SEL 挂载位置）。
        """
        # 搜索路径优先级列表
        configured = self.conf.get('LogServicesUri', '/redfish/v1/Managers/1/LogServices')
        candidates = [
            configured,
            '/redfish/v1/Systems/1/LogServices',
            '/redfish/v1/Chassis/1/LogServices',
        ]
        # 去重，保持顺序
        seen = set()
        search_paths = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                search_paths.append(p)

        for path in search_paths:
            CommonFunction.print_log('INFO', f'[SEL发现] 搜索 {path}')
            result = self._search_sel_in_log_services(client, path)
            if result:
                return result

        CommonFunction.print_log('WARNING',
            f'所有 LogServices 路径均未找到 SEL 条目（搜索范围：{search_paths}）')
        return None

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        if not client.login():
            self._rec(steps, all_pass, 'login', False, '登录失败')
            self.command_check_result = 'FAIL'
            self._save(test, steps);  return

        sel_uri   = self.conf.get('SelUri', '/redfish/v1/Managers/1/LogServices/SEL/Entries')
        req_fields = self.conf.get('RequiredEntryFields', ['Id', 'EntryType', 'Created'])

        # Step1: 先尝试配置的 URI
        code, body = client.get(sel_uri)
        if code == 404:
            CommonFunction.print_log('WARNING',
                f'配置 SelUri={sel_uri} 返回 404，尝试自动发现...')
            discovered = self._discover_sel_entries_uri(client)
            if discovered:
                sel_uri = discovered
                code, body = client.get(sel_uri)
            # 若仍然失败，沿用 404 结果触发 FAIL

        all_pass = self._rec(steps, all_pass, 'sel_accessible', code == 200,
                             f'HTTP={code}（URI={sel_uri}）')

        if code == 200 and isinstance(body, dict):
            members = body.get('Members', [])
            count   = body.get('Members@odata.count', len(members))
            steps.append(('sel_entry_count', 'PASS', f'{count}条'))
            # 检查前几条的字段
            for m in members[:3]:
                mid = m.get('@odata.id', '')
                if not mid:
                    continue
                mc, mb = client.get(mid)
                if mc == 200 and isinstance(mb, dict):
                    missing = [f for f in req_fields if f not in mb]
                    if missing:
                        all_pass = self._rec(steps, all_pass,
                            f'entry_fields_{mb.get("Id", "")}',
                            False, f'missing={missing}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)


if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebSelLog();  obj.run()
        exit_code = 0 if obj.command_check_result == 'PASS' else 2
        time.sleep(5)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log('ERROR', str(e));  traceback.print_exc();  exit_code = 1
    finally:
        if obj:
            try: open(obj.ec_path, 'w').write(str(exit_code))
            except: pass
    sys.exit(exit_code)
