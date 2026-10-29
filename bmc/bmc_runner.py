#!/bin/python
"""
BMC 自动化测试一键执行管理脚本（BmcRunner）

功能：
  - 按 suite 分组串行执行所有 bmc/ 脚本
  - 实时收集 stdout/stderr → runner.log（带脚本边界标记）
  - 从 result/ 读取 exit_code + JSON 结果
  - 扫描 runlog 提取 WARNING / ERROR 行
  - 输出 Markdown / HTML / Excel 三种格式测试报告

Usage:
  python3 -m bmc.bmc_runner -i <bmc_ip> -u <user> -p <pass>
  python3 -m bmc.bmc_runner -i <bmc_ip> -u <user> -p <pass> --suite chassis,ipmi
  python3 -m bmc.bmc_runner -i <bmc_ip> -u <user> -p <pass> --only chassis_001_drives_check
  python3 -m bmc.bmc_runner -i <bmc_ip> -u <user> -p <pass> --nostress
"""

import argparse
import json
import os
import time
import re
import subprocess
import sys
import traceback
from datetime import datetime

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────
SUITE_CONF  = os.path.join(os.path.dirname(__file__), '..', 'conf', 'bmc', 'bmc_runner_suites.json')
SCRIPT_CONF = os.path.join(os.path.dirname(__file__), '..', 'conf', 'bmc')
RESULT_BASE = os.path.join(os.path.dirname(__file__), '..', 'result', 'bmc')

# 退出码语义
# 注：EC_SKIP=3 为脚本主动跳过（如 systems_007 配置 EmptyLogPolicy=SKIP 且条目为空），
#     与机型差异跳过（SKIPPED）共用同一报告标签（issue/9 引入，思路源自上游 PR #13）
EC_PASS    = 0
EC_FAIL    = 2
EC_ERROR   = 1
EC_TIMEOUT = -1
EC_SKIP    = 3
EC_BLOCKED = 4
EC_RECOVERY_FAILED = 5
EC_LABELS  = {
    0: 'PASS', 2: 'FAIL', 1: 'ERROR', -1: 'TIMEOUT', 130: 'INTERRUPTED',
    3: 'SKIPPED', 4: 'BLOCKED', 5: 'FAIL',
}

# ANSI 颜色（终端输出）
C_GREEN  = '\033[32m'
C_RED    = '\033[31m'
C_YELLOW = '\033[33m'
C_CYAN   = '\033[36m'
C_RESET  = '\033[0m'
C_BOLD   = '\033[1m'

DEFAULT_SUITE_ORDER = [
    'chassis', 'systems', 'managers', 'account', 'session', 'update',
    'protocol', 'ipmi', 'web', 'event', 'stress', 'account_destructive',
]

# ─────────────────────────────────────────────
# 机型差异用例跳过机制（issue/9）
# ─────────────────────────────────────────────
# 并非所有机型都配有 GPU / 存储卡（如通算机型可能两者都没有，存储机型可能没有 GPU）。
# 这类用例本身的判定标准（资源列表为空即 FAIL）是正确的——真有硬件却检测不到仍应 FAIL；
# 只是「是否要跑这条用例」属于机型差异，不属于测试逻辑缺陷，因此在 runner 层提供
# --skip-no-gpu / --skip-no-storage 开关，由执行者按被测机型主动决定是否跳过。
SKIP_NO_GPU_SCRIPTS = {
    'systems_006_gpu_check',
}
SKIP_NO_STORAGE_SCRIPTS = {
    'systems_003a_storage_controller_check',
    'systems_003b_physical_drives_check',
}

# suite 名称到 conf 子目录的映射
SUITE_CONF_SUBDIR = {
    'chassis':  'chassis',
    'systems':  'systems',
    'managers': 'managers',
    'account':  'account',
    'account_destructive': 'account',
    'session':  'session',
    'update':   'update',
    'event':    'event',
    'ipmi':     'ipmi',
    'web':      'web',
    'stress':   'stress',
    'protocol': 'protocol',
    'custom':   '',          # --only 模式，fallback 到 conf/bmc/ 根目录
}

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def load_suite_conf():
    with open(SUITE_CONF, encoding='utf-8') as f:
        return json.load(f)

def load_script_meta(script_name, suite=''):
    """从 conf/bmc/<suite>/<script_name>.json 读取 TestNum / TestName / LogBasename

    查找优先级：
    1. conf/bmc/<suite>/<script_name>.json  （脚本独立配置）
    2. conf/bmc/<script_name>.json          （旧路径兼容）
    3. conf/bmc/<suite>/<suite>_test.json   （共用配置，按 LogBasename 匹配 section）
    4. conf/bmc/<suite>/<suite>.json        （备选共用配置名）
    """
    def _parse_single(d, script_name):
        """从单 key 的 json dict 解析 meta"""
        key = list(d.keys())[0]
        v = d[key]
        return {
            'TestNum':     v.get('TestNum', '?'),
            'TestName':    v.get('TestName', script_name),
            'LogBasename': v.get('LogBasename', script_name),
        }

    def _parse_shared(d, script_name):
        """从多 key 的共用 json dict 中按 LogBasename 匹配 section"""
        for v in d.values():
            if isinstance(v, dict) and v.get('LogBasename') == script_name:
                return {
                    'TestNum':     v.get('TestNum', '?'),
                    'TestName':    v.get('TestName', script_name),
                    'LogBasename': script_name,
                }
        return None

    _fallback = {'TestNum': '?', 'TestName': script_name, 'LogBasename': script_name}

    subdir = SUITE_CONF_SUBDIR.get(suite, '')

    # ── 1. 独立配置文件（suite 子目录）
    if subdir:
        conf_path = os.path.join(SCRIPT_CONF, subdir, f'{script_name}.json')
        if os.path.exists(conf_path):
            try:
                return _parse_single(json.load(open(conf_path, encoding='utf-8')), script_name)
            except Exception:
                pass

    # ── 2. 独立配置文件（根目录旧路径兼容）
    conf_path = os.path.join(SCRIPT_CONF, f'{script_name}.json')
    if os.path.exists(conf_path):
        try:
            return _parse_single(json.load(open(conf_path, encoding='utf-8')), script_name)
        except Exception:
            pass

    # ── 3/4. 共用配置文件（<suite>_test.json 或 <suite>.json）
    if subdir:
        for shared_name in (f'{suite}_test.json', f'{suite}.json'):
            shared_path = os.path.join(SCRIPT_CONF, subdir, shared_name)
            if os.path.exists(shared_path):
                try:
                    d = json.load(open(shared_path, encoding='utf-8'))
                    result = _parse_shared(d, script_name)
                    if result:
                        return result
                except Exception:
                    pass

    return _fallback

def read_exit_code(log_basename):
    """读取 result/bmc/<LogBasename>/exit_code 文件"""
    path = os.path.join(RESULT_BASE, log_basename, 'exit_code')
    try:
        return int(open(path).read().strip())
    except Exception:
        return None

def read_result_json(log_basename):
    """读取 result/bmc/<LogBasename>/<LogBasename>.json"""
    path = os.path.join(RESULT_BASE, log_basename, f'{log_basename}.json')
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return {}

def extract_log_lines(log_text, level):
    """从 runlog 文本提取指定级别的日志行（支持 WARNING / ERROR）"""
    pattern = re.compile(rf'\b{level}\b', re.IGNORECASE)
    return [line.strip() for line in log_text.splitlines() if pattern.search(line)]

def has_warning_in_log(log_text):
    return bool(re.search(r'\bWARNING\b', log_text, re.IGNORECASE))

def cprint(msg, color=''):
    print(f'{color}{msg}{C_RESET}' if color else msg)

# ─────────────────────────────────────────────
# 执行单条脚本
# ─────────────────────────────────────────────
def resolve_script_module(suite, script_name):
    """Return the importable module for a logical suite and script."""
    if suite and suite != 'custom':
        package = SUITE_CONF_SUBDIR.get(suite, suite)
        return f'bmc.{package}.{script_name}'
    return f'bmc.{script_name}'


def run_script(script_name, bmc_ip, username, password, timeout_sec, log_fh, suite=''):
    """执行一条脚本，返回 (exit_code, elapsed, stdout+stderr 文本)

    重构后脚本按 suite 分层，模块路径变为 bmc.<suite>.<script_name>。
    若 suite 为空（--only 模式）则尝试自动推断 suite，降级为 bmc.<script_name>。
    """
    module_path = resolve_script_module(suite, script_name)
    cmd = [sys.executable, '-m', module_path, '-i', bmc_ip, '-u', username, '-p', password]
    sep = '=' * 70
    header = f'\n{sep}\n>>> BEGIN {script_name}  [{datetime.now().strftime("%H:%M:%S")}]\n{sep}\n'
    log_fh.write(header)
    log_fh.flush()

    output_lines = []
    t0 = time.time()
    try:
        # 将当前 shell PATH 注入子进程，避免 Homebrew 等非系统路径缺失
        # （macOS 非登录 shell 不加载 ~/.zshrc，ipmitool 等工具会 FileNotFoundError）
        child_env = os.environ.copy()
        homebrew_bin = '/opt/homebrew/bin'
        homebrew_sbin = '/opt/homebrew/sbin'
        path_dirs = child_env.get('PATH', '').split(':')
        for extra in [homebrew_sbin, homebrew_bin]:
            if extra not in path_dirs:
                path_dirs.insert(0, extra)
        child_env['PATH'] = ':'.join(path_dirs)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=child_env,
        )
        # 实时读取并写入 runlog
        timed_out = False
        deadline = (t0 + timeout_sec) if timeout_sec and timeout_sec > 0 else None
        for line in proc.stdout:
            output_lines.append(line)
            log_fh.write(line)
            log_fh.flush()
            if deadline and time.time() > deadline:
                proc.kill()
                timed_out = True
                break
        proc.wait()
        elapsed = time.time() - t0
        if timed_out:
            log_fh.write(f'\n[RUNNER] 脚本执行超时（>{timeout_sec}s），已强杀\n')
            log_fh.flush()
            return EC_TIMEOUT, elapsed, ''.join(output_lines)
        # 使用进程退出码（不从 exit_code 文件读，后面再统一读文件）
        return proc.returncode, elapsed, ''.join(output_lines)
    except Exception as e:
        elapsed = time.time() - t0
        msg = f'[RUNNER] 执行异常: {e}\n'
        log_fh.write(msg)
        log_fh.flush()
        return EC_ERROR, elapsed, msg
    finally:
        footer = f'\n{sep}\n>>> END   {script_name}  [elapsed={time.time()-t0:.1f}s]\n{sep}\n'
        log_fh.write(footer)
        log_fh.flush()

# ─────────────────────────────────────────────
# 报告生成
# ─────────────────────────────────────────────
RESULT_ICON = {'PASS': '✅', 'FAIL': '❌', 'ERROR': '⚠️', 'TIMEOUT': '⏱️', 'INTERRUPTED': '🛑', 'SKIPPED': '⏭️', 'BLOCKED': '🚧', '?': '❓'}
RESULT_COLOR_HTML = {
    'PASS': '#52c41a', 'FAIL': '#ff4d4f', 'ERROR': '#fa8c16',
    'TIMEOUT': '#faad14', 'INTERRUPTED': '#722ed1', 'SKIPPED': '#8c8c8c',
    'BLOCKED': '#1677ff', '?': '#8c8c8c',
}

def _suite_stats(records, suite_order):
    """按 suite 聚合统计"""
    suite_map = {}
    for r in records:
        s = r['suite']
        if s not in suite_map:
            suite_map[s] = {'total': 0, 'PASS': 0, 'FAIL': 0, 'ERROR': 0,
                            'TIMEOUT': 0, 'SKIPPED': 0, 'BLOCKED': 0, 'WARN_PASS': 0}
        suite_map[s]['total'] += 1
        res = r['result']
        suite_map[s][res] = suite_map[s].get(res, 0) + 1
        if res == 'PASS' and r.get('has_warning'):
            suite_map[s]['WARN_PASS'] += 1
    return [(s, suite_map[s]) for s in suite_order if s in suite_map]

def generate_markdown(records, meta, out_path):
    ts       = meta['timestamp']
    bmc_ip   = meta['bmc_ip']
    total    = len(records)
    n_pass   = sum(1 for r in records if r['result'] == 'PASS')
    n_fail   = sum(1 for r in records if r['result'] == 'FAIL')
    n_err    = sum(1 for r in records if r['result'] in ('ERROR', 'TIMEOUT'))
    n_wp     = sum(1 for r in records if r['result'] == 'PASS' and r.get('has_warning'))
    n_skip   = sum(1 for r in records if r['result'] == 'SKIPPED')
    n_blocked = sum(1 for r in records if r['result'] == 'BLOCKED')
    suite_stats = _suite_stats(records, meta['suite_order'])

    lines = []
    lines.append(f'# BMC 自动化测试报告\n')
    lines.append(f'- **目标 BMC**：{bmc_ip}')
    lines.append(f'- **执行时间**：{ts}')
    lines.append(f'- **总计**：{total} 条　|　✅ PASS: {n_pass}　|　❌ FAIL: {n_fail}　|　⚠️ ERROR/TIMEOUT: {n_err}　|　🚧 BLOCKED: {n_blocked}　|　🔔 PASS含WARNING: {n_wp}　|　⏭️ 已跳过: {n_skip}')
    lines.append('')

    # 分组汇总表
    lines.append('## 分组汇总\n')
    lines.append('| 分组 | 总数 | PASS | FAIL | ERROR/TO | BLOCKED | PASS含WARNING | 已跳过 |')
    lines.append('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|')
    for suite, st in suite_stats:
        err_to = st.get('ERROR', 0) + st.get('TIMEOUT', 0)
        lines.append(f'| {suite} | {st["total"]} | {st["PASS"]} | {st["FAIL"]} | {err_to} | {st.get("BLOCKED", 0)} | {st["WARN_PASS"]} | {st.get("SKIPPED", 0)} |')
    lines.append('')

    # ⏭️ 已跳过明细（机型差异）
    skipped = [r for r in records if r['result'] == 'SKIPPED']
    if skipped:
        lines.append('## ⏭️ 已跳过用例（机型差异）\n')
        lines.append('| 脚本名 | TestNum | 测试名称 | 跳过原因 |')
        lines.append('|:---|:---|:---|:---|')
        for r in skipped:
            lines.append(f'| {r["script"]} | {r["test_num"]} | {r["test_name"]} | {r.get("skip_reason", "")} |')
        lines.append('')

    # 🔔 PASS 含 WARNING 提醒
    warn_pass = [r for r in records if r['result'] == 'PASS' and r.get('has_warning')]
    if warn_pass:
        lines.append('## 🔔 PASS 含 WARNING 项（需人工确认）\n')
        for r in warn_pass:
            lines.append(f'### {r["script"]}（{r["test_num"]}）')
            lines.append(f'- **测试名称**：{r["test_name"]}')
            lines.append(f'- **耗时**：{r["elapsed"]:.1f}s')
            lines.append('- **WARNING 日志**：')
            for w in r.get('warning_lines', [])[:10]:
                lines.append(f'  ```\n  {w}\n  ```')
            lines.append('')

    # ❌ FAIL / ERROR 详情（含 ERROR 日志）
    failed = [r for r in records if r['result'] in ('FAIL', 'ERROR', 'TIMEOUT', 'BLOCKED')]
    if failed:
        lines.append('## ❌ 失败/异常详情\n')
        for r in failed:
            icon = RESULT_ICON.get(r['result'], '?')
            lines.append(f'### {icon} {r["script"]}（{r["test_num"]}）— {r["result"]}')
            lines.append(f'- **测试名称**：{r["test_name"]}')
            lines.append(f'- **耗时**：{r["elapsed"]:.1f}s')
            err_lines = r.get('error_lines', [])
            if err_lines:
                lines.append('- **ERROR 日志**：')
                for e in err_lines[:15]:
                    lines.append(f'  ```\n  {e}\n  ```')
            lines.append('')

    # 完整结果表
    lines.append('## 各脚本完整结果\n')
    lines.append('| 结果 | 脚本名 | TestNum | 测试名称 | 耗时 | 备注 |')
    lines.append('|:---:|:---|:---|:---|:---:|:---|')
    for r in records:
        icon = RESULT_ICON.get(r['result'], '?')
        note = '🔔 含WARNING' if r['result'] == 'PASS' and r.get('has_warning') else ''
        lines.append(f'| {icon} {r["result"]} | {r["script"]} | {r["test_num"]} | {r["test_name"]} | {r["elapsed"]:.1f}s | {note} |')
    lines.append('')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

def generate_html(records, meta, out_path):
    ts     = meta['timestamp']
    bmc_ip = meta['bmc_ip']
    total  = len(records)
    n_pass = sum(1 for r in records if r['result'] == 'PASS')
    n_fail = sum(1 for r in records if r['result'] == 'FAIL')
    n_err  = sum(1 for r in records if r['result'] in ('ERROR', 'TIMEOUT'))
    n_wp   = sum(1 for r in records if r['result'] == 'PASS' and r.get('has_warning'))
    n_skip = sum(1 for r in records if r['result'] == 'SKIPPED')
    n_blocked = sum(1 for r in records if r['result'] == 'BLOCKED')
    suite_stats = _suite_stats(records, meta['suite_order'])

    def esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    rows_suite = ''
    for suite, st in suite_stats:
        err_to = st.get('ERROR', 0) + st.get('TIMEOUT', 0)
        rows_suite += (
            f'<tr><td><b>{suite}</b></td><td>{st["total"]}</td>'
            f'<td style="color:#52c41a">{st["PASS"]}</td>'
            f'<td style="color:#ff4d4f">{st["FAIL"]}</td>'
            f'<td style="color:#fa8c16">{err_to}</td>'
            f'<td style="color:#1677ff">{st.get("BLOCKED", 0)}</td>'
            f'<td style="color:#faad14">{st["WARN_PASS"]}</td>'
            f'<td style="color:#8c8c8c">{st.get("SKIPPED", 0)}</td></tr>\n'
        )

    skip_section = ''
    for r in records:
        if r['result'] == 'SKIPPED':
            skip_section += f'''
<div class="detail-block skip-block">
  <div class="detail-title" style="color:#8c8c8c">⏭️ {esc(r["script"])} <span class="badge" style="background:#8c8c8c">SKIPPED</span></div>
  <div class="detail-meta">TestNum: {esc(r["test_num"])} | {esc(r["test_name"])} | 跳过原因: {esc(r.get("skip_reason",""))}</div>
</div>'''

    warn_section = ''
    for r in records:
        if r['result'] == 'PASS' and r.get('has_warning'):
            wlines = ''.join(f'<div class="log-line warn">{esc(w)}</div>' for w in r.get('warning_lines', [])[:10])
            warn_section += f'''
<div class="detail-block warn-block">
  <div class="detail-title">🔔 {esc(r["script"])} <span class="badge badge-warn">PASS含WARNING</span></div>
  <div class="detail-meta">TestNum: {esc(r["test_num"])} | 耗时: {r["elapsed"]:.1f}s | {esc(r["test_name"])}</div>
  <div class="log-box">{wlines}</div>
</div>'''

    fail_section = ''
    for r in records:
        if r['result'] in ('FAIL', 'ERROR', 'TIMEOUT', 'BLOCKED'):
            color = RESULT_COLOR_HTML.get(r['result'], '#8c8c8c')
            elines = ''.join(f'<div class="log-line err">{esc(e)}</div>' for e in r.get('error_lines', [])[:15])
            fail_section += f'''
<div class="detail-block fail-block">
  <div class="detail-title" style="color:{color}">
    {RESULT_ICON.get(r["result"],"?")} {esc(r["script"])} <span class="badge" style="background:{color}">{r["result"]}</span>
  </div>
  <div class="detail-meta">TestNum: {esc(r["test_num"])} | 耗时: {r["elapsed"]:.1f}s | {esc(r["test_name"])}</div>
  <div class="log-box">{elines if elines else "<span style='color:#aaa'>（无 ERROR 日志）</span>"}</div>
</div>'''

    all_rows = ''
    for r in records:
        color = RESULT_COLOR_HTML.get(r['result'], '#8c8c8c')
        note = '<span style="color:#faad14">🔔 含WARNING</span>' if r['result'] == 'PASS' and r.get('has_warning') else ''
        all_rows += (
            f'<tr>'
            f'<td style="color:{color};font-weight:bold">{RESULT_ICON.get(r["result"],"?")} {esc(r["result"])}</td>'
            f'<td>{esc(r["suite"])}</td>'
            f'<td style="font-family:monospace">{esc(r["script"])}</td>'
            f'<td>{esc(r["test_num"])}</td>'
            f'<td>{esc(r["test_name"])}</td>'
            f'<td>{r["elapsed"]:.1f}s</td>'
            f'<td>{note}</td>'
            f'</tr>\n'
        )

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>BMC 自动化测试报告 - {esc(bmc_ip)}</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 0; background: #f0f2f5; color: #222; }}
  .container {{ max-width: 1400px; margin: 24px auto; padding: 0 24px; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #1890ff; padding-bottom: 8px; }}
  h2 {{ color: #1a1a2e; margin-top: 32px; }}
  .summary-cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 16px 0 24px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 16px 24px; min-width: 140px;
           box-shadow: 0 2px 8px rgba(0,0,0,.08); text-align: center; }}
  .card .num {{ font-size: 32px; font-weight: bold; }}
  .card .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px;
           box-shadow: 0 2px 8px rgba(0,0,0,.08); overflow: hidden; }}
  th {{ background: #1890ff; color: #fff; padding: 10px 12px; text-align: left; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
  tr:hover td {{ background: #e6f7ff; }}
  .detail-block {{ background: #fff; border-radius: 8px; margin: 12px 0;
                   padding: 16px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .warn-block {{ border-left: 4px solid #faad14; }}
  .fail-block {{ border-left: 4px solid #ff4d4f; }}
  .detail-title {{ font-size: 15px; font-weight: bold; margin-bottom: 4px; }}
  .detail-meta {{ font-size: 12px; color: #888; margin-bottom: 8px; }}
  .log-box {{ background: #1e1e1e; border-radius: 4px; padding: 8px 12px;
              max-height: 240px; overflow-y: auto; }}
  .log-line {{ font-family: monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; }}
  .log-line.warn {{ color: #faad14; }}
  .log-line.err  {{ color: #ff7875; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
            color: #fff; margin-left: 8px; }}
  .badge-warn {{ background: #faad14; }}
  .skip-block {{ border-left: 4px solid #8c8c8c; }}
  .meta-info {{ background: #fff; border-radius: 8px; padding: 16px 24px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08); margin-bottom: 24px; }}
  .meta-info p {{ margin: 4px 0; font-size: 14px; }}
</style>
</head>
<body>
<div class="container">
  <h1>BMC 自动化测试报告</h1>
  <div class="meta-info">
    <p>🖥️ <b>目标 BMC</b>：{esc(bmc_ip)}</p>
    <p>🕐 <b>执行时间</b>：{esc(ts)}</p>
  </div>

  <div class="summary-cards">
    <div class="card"><div class="num" style="color:#1890ff">{total}</div><div class="label">总计</div></div>
    <div class="card"><div class="num" style="color:#52c41a">{n_pass}</div><div class="label">✅ PASS</div></div>
    <div class="card"><div class="num" style="color:#ff4d4f">{n_fail}</div><div class="label">❌ FAIL</div></div>
    <div class="card"><div class="num" style="color:#fa8c16">{n_err}</div><div class="label">⚠️ ERROR/TO</div></div>
    <div class="card"><div class="num" style="color:#1677ff">{n_blocked}</div><div class="label">🚧 BLOCKED</div></div>
    <div class="card"><div class="num" style="color:#faad14">{n_wp}</div><div class="label">🔔 PASS含WARNING</div></div>
    <div class="card"><div class="num" style="color:#8c8c8c">{n_skip}</div><div class="label">⏭️ 已跳过</div></div>
  </div>

  <h2>分组汇总</h2>
  <table>
    <tr><th>分组</th><th>总数</th><th>PASS</th><th>FAIL</th><th>ERROR/TIMEOUT</th><th>BLOCKED</th><th>PASS含WARNING</th><th>已跳过</th></tr>
    {rows_suite}
  </table>

  {'<h2>🔔 PASS 含 WARNING 项（需人工确认）</h2>' + warn_section if warn_section else ''}

  {'<h2>❌ 失败/异常详情</h2>' + fail_section if fail_section else ''}

  {'<h2>⏭️ 已跳过用例（机型差异）</h2>' + skip_section if skip_section else ''}

  <h2>各脚本完整结果</h2>
  <table>
    <tr><th>结果</th><th>分组</th><th>脚本名</th><th>TestNum</th><th>测试名称</th><th>耗时</th><th>备注</th></tr>
    {all_rows}
  </table>
</div>
</body>
</html>'''

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

def generate_excel(records, meta, out_path):
    """生成 Excel 报告（使用 openpyxl，降级到 CSV）"""
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()

        # ── Sheet1: 汇总 ──
        ws1 = wb.active
        ws1.title = '汇总'
        ws1.append(['BMC IP', meta['bmc_ip']])
        ws1.append(['执行时间', meta['timestamp']])
        ws1.append([])

        total  = len(records)
        n_pass = sum(1 for r in records if r['result'] == 'PASS')
        n_fail = sum(1 for r in records if r['result'] == 'FAIL')
        n_err  = sum(1 for r in records if r['result'] in ('ERROR', 'TIMEOUT'))
        n_wp   = sum(1 for r in records if r['result'] == 'PASS' and r.get('has_warning'))
        n_skip = sum(1 for r in records if r['result'] == 'SKIPPED')
        n_blocked = sum(1 for r in records if r['result'] == 'BLOCKED')

        for label, val in [('总计', total), ('PASS', n_pass), ('FAIL', n_fail),
                            ('ERROR/TIMEOUT', n_err), ('BLOCKED', n_blocked),
                            ('PASS含WARNING', n_wp), ('已跳过', n_skip)]:
            ws1.append([label, val])
        ws1.append([])

        suite_stats = _suite_stats(records, meta['suite_order'])
        ws1.append(['分组', '总数', 'PASS', 'FAIL', 'ERROR/TIMEOUT', 'BLOCKED', 'PASS含WARNING', '已跳过'])
        for suite, st in suite_stats:
            err_to = st.get('ERROR', 0) + st.get('TIMEOUT', 0)
            ws1.append([suite, st['total'], st['PASS'], st['FAIL'], err_to,
                        st.get('BLOCKED', 0), st['WARN_PASS'], st.get('SKIPPED', 0)])

        # ── Sheet2: 完整结果 ──
        ws2 = wb.create_sheet('测试结果')
        headers = ['结果', '分组', '脚本名', 'TestNum', '测试名称', '耗时(s)', '含WARNING', 'ERROR日志（摘要）']
        ws2.append(headers)

        COLOR_FILL = {
            'PASS':    'C6EFCE', 'FAIL':    'FFC7CE',
            'ERROR':   'FFEB9C', 'TIMEOUT': 'FFEB9C',
            'SKIPPED': 'E0E0E0',
            'BLOCKED': 'D6E4FF',
        }
        thin = Side(style='thin', color='DDDDDD')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for r in records:
            err_summary = ' | '.join(r.get('error_lines', [])[:3])
            ws2.append([
                r['result'], r['suite'], r['script'], r['test_num'], r['test_name'],
                round(r['elapsed'], 1),
                '是' if r.get('has_warning') else '',
                err_summary[:200],
            ])
            row_idx = ws2.max_row
            fill_color = COLOR_FILL.get(r['result'], 'FFFFFF')
            fill = PatternFill(fill_type='solid', fgColor=fill_color)
            for col in range(1, len(headers) + 1):
                cell = ws2.cell(row=row_idx, column=col)
                cell.fill = fill
                cell.border = border

        # 调整列宽
        for col_idx, width in enumerate([10, 10, 40, 20, 30, 10, 10, 60], 1):
            ws2.column_dimensions[get_column_letter(col_idx)].width = width

        # 表头加粗
        for cell in ws2[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill(fill_type='solid', fgColor='1890FF')
            cell.font = Font(bold=True, color='FFFFFF')

        # ── Sheet3: WARNING 项 ──
        ws3 = wb.create_sheet('WARNING项')
        ws3.append(['脚本名', 'TestNum', '测试名称', '耗时(s)', 'WARNING日志'])
        for r in records:
            if r['result'] == 'PASS' and r.get('has_warning'):
                wlines = '\n'.join(r.get('warning_lines', [])[:10])
                ws3.append([r['script'], r['test_num'], r['test_name'], round(r['elapsed'], 1), wlines])

        # ── Sheet4: FAIL 详情 ──
        ws4 = wb.create_sheet('FAIL详情')
        ws4.append(['脚本名', 'TestNum', '测试名称', '结果', '耗时(s)', 'ERROR日志'])
        for r in records:
            if r['result'] in ('FAIL', 'ERROR', 'TIMEOUT', 'BLOCKED'):
                elines = '\n'.join(r.get('error_lines', [])[:15])
                ws4.append([r['script'], r['test_num'], r['test_name'], r['result'],
                             round(r['elapsed'], 1), elines])

        wb.save(out_path)
        return True
    except ImportError:
        # openpyxl 不可用，降级 CSV
        csv_path = out_path.replace('.xlsx', '.csv')
        with open(csv_path, 'w', encoding='utf-8-sig') as f:
            f.write('结果,分组,脚本名,TestNum,测试名称,耗时(s),含WARNING,ERROR日志\n')
            for r in records:
                err_summary = ' | '.join(r.get('error_lines', [])[:3]).replace('"', "'")
                f.write(f'"{r["result"]}","{r["suite"]}","{r["script"]}","{r["test_num"]}",'
                        f'"{r["test_name"]}",{r["elapsed"]:.1f},'
                        f'{"是" if r.get("has_warning") else ""},"{err_summary[:200]}"\n')
        return False

# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='BMC 自动化测试一键执行管理脚本')
    parser.add_argument('-i', '--bmc_ip',    required=True)
    parser.add_argument('-u', '--user_name', required=True)
    parser.add_argument('-p', '--password',  required=True)
    parser.add_argument('--suite',   default='',  help='指定分组，逗号分隔，如 chassis,ipmi（默认跑除 stress 外所有）')
    parser.add_argument('--only',    default='',  help='只跑某个脚本名（调试用），如 chassis_001_drives_check')
    parser.add_argument('--nostress',action='store_true', help='屏蔽 stress 压测脚本')
    parser.add_argument('--skip-no-gpu', action='store_true',
                         help='被测机型无 GPU（如通算/存储机型），跳过 systems_006_gpu_check')
    parser.add_argument('--skip-no-storage', action='store_true',
                         help='被测机型无存储卡（如通算/GPU 机型），跳过 systems_003a/003b 存储检查用例')
    args = parser.parse_args()

    conf = load_suite_conf()
    suites_def  = conf['suites']
    timeouts    = conf.get('timeouts', {})
    default_to  = timeouts.get('default', 120)

    # 构建脚本名 → suite 的反向映射（用于 --only 模式自动推断 suite）
    script_to_suite = {}
    for suite_name, script_list in suites_def.items():
        for s in script_list:
            script_to_suite[s] = suite_name

    # 确定要跑的脚本列表
    if args.only:
        inferred_suite = script_to_suite.get(args.only, 'custom')
        scripts_to_run = [(inferred_suite, args.only)]
    else:
        if args.suite:
            suite_names = [s.strip() for s in args.suite.split(',') if s.strip()]
        else:
            suite_names = list(DEFAULT_SUITE_ORDER)

        if args.nostress and 'stress' in suite_names:
            suite_names.remove('stress')

        scripts_to_run = []
        for suite in suite_names:
            for script in suites_def.get(suite, []):
                scripts_to_run.append((suite, script))

    # 机型差异跳过（issue/9）：--skip-no-gpu / --skip-no-storage
    skipped_records = []
    if args.skip_no_gpu or args.skip_no_storage:
        filtered = []
        for suite, script in scripts_to_run:
            if args.skip_no_gpu and script in SKIP_NO_GPU_SCRIPTS:
                skipped_records.append((suite, script, '机型无 GPU（--skip-no-gpu）'))
                continue
            if args.skip_no_storage and script in SKIP_NO_STORAGE_SCRIPTS:
                skipped_records.append((suite, script, '机型无存储卡（--skip-no-storage）'))
                continue
            filtered.append((suite, script))
        scripts_to_run = filtered

    if not scripts_to_run and not skipped_records:
        print('没有需要执行的脚本，退出。')
        sys.exit(0)

    # 创建输出目录
    ts_str  = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.join(RESULT_BASE, f'bmc_runner_{ts_str}')
    os.makedirs(run_dir, exist_ok=True)

    log_path  = os.path.join(run_dir, 'runner.log')
    md_path   = os.path.join(run_dir, 'report.md')
    html_path = os.path.join(run_dir, 'report.html')
    xlsx_path = os.path.join(run_dir, 'report.xlsx')
    json_path = os.path.join(run_dir, 'summary.json')

    cprint(f'\n{C_BOLD}{"="*60}', C_CYAN)
    cprint(f'  BMC 自动化测试 Runner', C_CYAN)
    cprint(f'  BMC IP : {args.bmc_ip}', C_CYAN)
    cprint(f'  脚本数 : {len(scripts_to_run)}', C_CYAN)
    if skipped_records:
        cprint(f'  已跳过 : {len(skipped_records)}（机型差异，见下方明细）', C_YELLOW)
        for s_suite, s_script, s_reason in skipped_records:
            cprint(f'    ⏭️  {s_suite}/{s_script} — {s_reason}', C_YELLOW)
    cprint(f'  输出目录: {run_dir}', C_CYAN)
    cprint(f'{"="*60}{C_RESET}')

    records = []
    suite_order_seen = []
    recovery_failed = False

    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(f'BMC Runner 开始 | BMC={args.bmc_ip} | 时间={datetime.now()}\n\n')

        for idx, (suite, script_name) in enumerate(scripts_to_run, 1):
            if suite not in suite_order_seen:
                suite_order_seen.append(suite)

            meta_info = load_script_meta(script_name, suite)
            timeout   = timeouts.get(script_name, default_to)

            cprint(f'\n[{idx:03d}/{len(scripts_to_run)}] {suite}/{script_name}', C_CYAN)

            if recovery_failed:
                reason = 'A destructive account test could not restore active credentials'
                cprint(f'  → BLOCKED  {reason}', C_YELLOW)
                records.append({
                    'suite': suite, 'script': script_name,
                    'test_num': meta_info['TestNum'], 'test_name': meta_info['TestName'],
                    'result': 'BLOCKED', 'elapsed': 0.0, 'exit_code': EC_BLOCKED,
                    'has_warning': False, 'warning_lines': [], 'error_lines': [],
                    'blocked_reason': reason,
                })
                continue

            ec, elapsed, output = run_script(
                script_name, args.bmc_ip, args.user_name, args.password, timeout, log_fh, suite
            )

            # 优先读 exit_code 文件（更准确，由脚本内部写入）
            file_ec = read_exit_code(meta_info['LogBasename'])
            final_ec = file_ec if file_ec is not None else ec
            if final_ec == EC_RECOVERY_FAILED:
                recovery_failed = True

            result = EC_LABELS.get(final_ec, 'ERROR')

            # 扫描日志
            warning_lines = extract_log_lines(output, 'WARNING')
            error_lines   = extract_log_lines(output, 'ERROR')
            has_warning   = bool(warning_lines)

            # 终端输出
            color = C_GREEN if result == 'PASS' else C_RED if result == 'FAIL' else C_YELLOW
            warn_tag = f' {C_YELLOW}[🔔 含WARNING]{C_RESET}' if result == 'PASS' and has_warning else ''
            cprint(f'  → {result}  耗时={elapsed:.1f}s{warn_tag}', color)

            record = {
                'suite':         suite,
                'script':        script_name,
                'test_num':      meta_info['TestNum'],
                'test_name':     meta_info['TestName'],
                'result':        result,
                'elapsed':       elapsed,
                'exit_code':     final_ec,
                'has_warning':   has_warning,
                'warning_lines': warning_lines,
                'error_lines':   error_lines,
            }
            records.append(record)

    # 机型差异跳过的脚本也计入 records（result='SKIPPED'），避免在报告中静默消失
    for s_suite, s_script, s_reason in skipped_records:
        if s_suite not in suite_order_seen:
            suite_order_seen.append(s_suite)
        s_meta = load_script_meta(s_script, s_suite)
        records.append({
            'suite':         s_suite,
            'script':        s_script,
            'test_num':      s_meta['TestNum'],
            'test_name':     s_meta['TestName'],
            'result':        'SKIPPED',
            'elapsed':       0.0,
            'exit_code':     None,
            'has_warning':   False,
            'warning_lines': [],
            'error_lines':   [],
            'skip_reason':   s_reason,
        })

    # 最终汇总
    n_pass = sum(1 for r in records if r['result'] == 'PASS')
    n_fail = sum(1 for r in records if r['result'] == 'FAIL')
    n_err  = sum(1 for r in records if r['result'] in ('ERROR', 'TIMEOUT'))
    n_wp   = sum(1 for r in records if r['result'] == 'PASS' and r.get('has_warning'))
    n_skip = sum(1 for r in records if r['result'] == 'SKIPPED')
    n_blocked = sum(1 for r in records if r['result'] == 'BLOCKED')

    cprint(f'\n{"="*60}', C_CYAN)
    cprint(f'  完成！总计={len(records)}  PASS={n_pass}  FAIL={n_fail}  ERROR/TO={n_err}  BLOCKED={n_blocked}  PASS含WARNING={n_wp}  已跳过={n_skip}', C_BOLD)
    cprint(f'{"="*60}', C_CYAN)

    report_meta = {
        'timestamp':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'bmc_ip':      args.bmc_ip,
        'suite_order': suite_order_seen,
        'total':       len(records),
        'pass':        n_pass,
        'fail':        n_fail,
        'error':       n_err,
        'warn_pass':   n_wp,
        'skipped':     n_skip,
        'blocked':     n_blocked,
    }

    # 写 summary.json
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'meta': report_meta, 'records': [
            {k: v for k, v in r.items() if k not in ('warning_lines', 'error_lines')}
            for r in records
        ]}, f, ensure_ascii=False, indent=2)

    # 生成报告
    generate_markdown(records, report_meta, md_path)
    cprint(f'  📄 Markdown : {md_path}', C_CYAN)

    generate_html(records, report_meta, html_path)
    cprint(f'  🌐 HTML     : {html_path}', C_CYAN)

    ok_xlsx = generate_excel(records, report_meta, xlsx_path)
    if ok_xlsx:
        cprint(f'  📊 Excel    : {xlsx_path}', C_CYAN)
    else:
        cprint(f'  📊 Excel(降级CSV): {xlsx_path.replace(".xlsx",".csv")}', C_YELLOW)

    cprint(f'  📝 RunLog   : {log_path}', C_CYAN)

    # 整体退出码
    sys.exit(0 if n_fail == 0 and n_err == 0 and n_blocked == 0 else 2)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        cprint('\n[Runner] 用户中断', C_YELLOW)
        sys.exit(130)
    except Exception as e:
        cprint(f'\n[Runner] 异常: {e}', C_RED)
        traceback.print_exc()
        sys.exit(1)
