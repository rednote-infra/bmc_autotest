#!/bin/python
"""
BMC Web HTTP稳定性压测（Web_016）
持续请求多个Web接口，统计成功率/响应时间

Usage: python3 -m bmc.web_016_http_stress -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback, statistics
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebHttpStress(BmcWebTestBase):
    CASE        = 'WebHttpStress'
    CONFIG_FILE = 'web_016_http_stress.json'

    def _load_extra_config(self, conf: dict) -> None:
        self.conf = conf

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        if not client.login():
            self._rec(steps, all_pass, 'login', False, '登录失败')
            self.command_check_result = 'FAIL'
            self._save(test, steps);  return

        duration = self.conf.get('DurationSeconds', 300)
        interval = self.conf.get('IntervalSeconds', 2)
        max_resp = self.conf.get('MaxResponseSec', 5.0)
        uris = self.conf.get('StressUris', ['/redfish/v1/Systems/1'])

        total = 0;  failed = 0;  slow = 0
        latencies = []
        t_end = time.monotonic() + duration
        uri_idx = 0

        CommonFunction.print_log('INFO', f'开始HTTP稳定性压测，时长={duration}s，接口数={len(uris)}')
        while time.monotonic() < t_end:
            uri = uris[uri_idx % len(uris)]
            code, body, elapsed = client.timed_get(uri)
            total += 1
            if code != 200:
                failed += 1
                CommonFunction.print_log('WARNING', f'[{total}] {uri} HTTP={code}')
            if elapsed > max_resp:
                slow += 1
            latencies.append(elapsed)
            uri_idx += 1
            remain = t_end - time.monotonic()
            if remain > 0:
                time.sleep(min(interval, remain))

        client.logout()

        # 统计
        p50: float = statistics.median(latencies) if latencies else 0.0
        if len(latencies) > 1:
            p99_idx = min(int(len(latencies) * 0.99), len(latencies) - 1)
            p99: float = sorted(latencies)[p99_idx]
        else:
            p99 = latencies[0] if latencies else 0.0
        success_rate = (total - failed) / total * 100 if total > 0 else 0.0
        slow_rate = slow / total * 100 if total > 0 else 0.0

        summary = (f'总请求={total}, 失败={failed}, 成功率={success_rate:.1f}%, '
                   f'慢请求(>{max_resp}s)={slow}({slow_rate:.1f}%), '
                   f'P50={p50:.3f}s, P99={p99:.3f}s')
        CommonFunction.print_log('INFO', summary)

        # PASS 标准：失败次数=0，且无超时请求
        pass_fail  = failed == 0
        pass_slow  = slow == 0
        all_pass = self._rec(steps, all_pass, 'no_connection_failures', pass_fail,
            f'failed={failed}/{total}')
        all_pass = self._rec(steps, all_pass, f'no_slow_response_over_{max_resp}s', pass_slow,
            f'slow={slow}/{total}')
        steps.append(('stress_summary', 'PASS', summary))

        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebHttpStress();  obj.run()
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
