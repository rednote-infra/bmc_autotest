"""
bmc_diag.py — BMC 测试诊断辅助模块

功能：
  1. attach_sdk_logger(log_file, logger_name)
     挂载 redfish_sdk.http_client 的 DEBUG handler，让所有 HTTP 请求（URL/body/
     状态码/响应体）自动写入测试 log 文件，无需改任何业务脚本。

  2. diagnose(exc, context, status_code, response_body)
     在 WARNING / ERROR 发生时提供初步定位提示，根据 HTTP 状态码 + 异常类型 +
     响应体关键词给出人类可读的诊断说明，辅助 DEBUG 缺陷根因。

使用方式
--------
在 _initialize_log_system() / _init_log() 末尾调用：
    from func.bmc_diag import attach_sdk_logger
    attach_sdk_logger(log_file_path, logger_name=self.LOG_BASE_NAME)

在 except 块中调用：
    from func.bmc_diag import diagnose
    diagnose(exc, context="GET /redfish/v1/Chassis/1/Drives")
"""

import logging
import os
import sys
from typing import Optional, Any

# ─────────────────────────────────────────────────────────────────────────────
# HTTP 状态码诊断规则表
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_HINTS: dict[int, str] = {
    # 4xx 客户端错误
    400: (
        "[诊断] HTTP 400 Bad Request — 请求体格式有误或字段值不合法。"
        "请检查：① PATCH/POST body 中字段名/枚举值是否正确 ② 是否缺少必填字段"
    ),
    401: (
        "[诊断] HTTP 401 Unauthorized — 认证失败。"
        "请检查：① BMC IP / 用户名 / 密码是否正确 ② Session 是否已超时或被清除"
        " ③ 是否尝试用已删除账号的凭据连接"
    ),
    403: (
        "[诊断] HTTP 403 Forbidden — 权限不足或资源受保护。"
        "请检查：① 当前账号角色是否有写权限（Administrator/Operator）"
        " ② 此操作是否被 BMC 安全策略明确禁止（如删除超管）"
    ),
    404: (
        "[诊断] HTTP 404 Not Found — 资源路径不存在。"
        "请检查：① URI 是否拼写正确 ② 该硬件组件是否实际存在（如无 GPU 时"
        " /Systems/1/Processors/GPU0 会 404） ③ 该功能是否被当前 BMC 固件支持"
    ),
    405: (
        "[诊断] HTTP 405 Method Not Allowed — HTTP 方法不被允许。"
        "请检查：① 是否对只读资源执行了 PATCH/DELETE ② 部分 BMC 接口只支持"
        " POST 而非 PATCH，需确认厂商接口规范"
    ),
    408: (
        "[诊断] HTTP 408 Request Timeout — BMC 处理超时。"
        "常见于：① 固件升级类操作 ② BMC Reset 后的等待期 ③ 网络抖动"
        " — 建议增加 sleep 等待后重试"
    ),
    409: (
        "[诊断] HTTP 409 Conflict — 资源冲突。"
        "常见于：① 已存在同名账号时再次创建 ② BIOS 设置冲突"
        " ③ 同时有多个 BMC Reset 任务在执行"
    ),
    412: (
        "[诊断] HTTP 412 Precondition Failed — ETag 校验失败（If-Match 不匹配）。"
        "资源在 GET 之后被其他请求修改过，SDK 缓存的 ETag 已失效。"
        "通常可重新 GET 后再 PATCH 解决。"
    ),
    422: (
        "[诊断] HTTP 422 Unprocessable Entity — 语义错误。"
        "请检查：① 字段值超出允许范围 ② 密码不满足复杂度规则 ③ 设置值与当前状态冲突"
    ),
    500: (
        "[诊断] HTTP 500 Internal Server Error — BMC 内部错误。"
        "可能是：① BMC 固件 Bug ② 硬件异常导致数据不可读 ③ 并发请求冲击"
        " — 建议记录为 BMC 端问题，单独重现后提交固件 Bug"
    ),
    503: (
        "[诊断] HTTP 503 Service Unavailable — BMC 服务暂时不可用。"
        "通常出现在：① BMC Reset 后的启动期 ② 固件刷新中途"
        " — 建议等待 60～120s 后重试"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# 响应体关键词诊断规则（匹配 body 字符串中的关键词，输出额外提示）
# ─────────────────────────────────────────────────────────────────────────────

_BODY_KEYWORD_HINTS: list[tuple[str, str]] = [
    ("PropertyNotWritable",
     "[诊断] BMC 返回 PropertyNotWritable — 尝试修改只读属性，请确认该字段在此厂商固件中是否可写"),
    ("PropertyUnknown",
     "[诊断] BMC 返回 PropertyUnknown — 请求体包含 BMC 不认识的字段，请检查字段名是否正确"),
    ("PropertyValueNotInList",
     "[诊断] BMC 返回 PropertyValueNotInList — 枚举值不在允许范围内，请核对 AllowableValues"),
    ("PropertyValueFormatError",
     "[诊断] BMC 返回 PropertyValueFormatError — 字段值格式不正确（如日期/数字格式）"),
    ("ActionNotSupported",
     "[诊断] BMC 返回 ActionNotSupported — 此 Action 在当前固件版本中不支持"),
    ("InsufficientPrivilege",
     "[诊断] BMC 返回 InsufficientPrivilege — 当前账号权限不足，需要 Administrator 角色"),
    ("AccountForSessionNoLongerExists",
     "[诊断] 账号已被删除，Session 失效（HTTP 401）— 可能是反向测试误删了账号"),
    ("MaximumNumberOfUsers",
     "[诊断] BMC 返回 MaximumNumberOfUsers — 账号数已达上限，需先删除已有账号再创建"),
    ("PasswordChangeRequired",
     "[诊断] BMC 要求修改初始密码后才能继续操作，当前账号处于临时状态"),
    ("ServiceTemporarilyUnavailable",
     "[诊断] BMC 服务临时不可用，通常是 Reset 后的启动期（建议等待后重试）"),
    ("Username is too long",
     "[诊断] IPMI 报告用户名超长 — 需求要求 ≤16 字符，BMC 拒绝注册"),
    ("Invalid data field in request",
     "[诊断] IPMI 报告无效数据字段 — ipmitool 命令参数或格式错误"),
    ("timeout",
     "[诊断] 请求超时 — 可能原因：① 网络不通 ② BMC 负载高 ③ 脚本超时配置过短"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 异常类型诊断规则
# ─────────────────────────────────────────────────────────────────────────────

_EXCEPTION_HINTS: dict[str, str] = {
    "RedfishConnectionError": (
        "[诊断] RedfishConnectionError — 无法连接到 BMC。"
        "请检查：① BMC IP 是否可达（ping 测试） ② 网络路由/防火墙配置 ③ BMC 是否已重启完成"
    ),
    "RedfishTimeoutError": (
        "[诊断] RedfishTimeoutError — 请求超时。"
        "常见于：① BMC Reset 后启动期 ② 固件升级进行中 ③ 网络延迟过高"
        " — 可在 bmc_runner_suites.json 中增加该用例的 timeout 值"
    ),
    "RedfishAuthError": (
        "[诊断] RedfishAuthError — 认证失败（HTTP 401/403）。"
        "请检查：① 凭据是否正确 ② 当前账号是否被删除/禁用 ③ 测试顺序是否影响账号状态"
    ),
    "RedfishNotFoundError": (
        "[诊断] RedfishNotFoundError — 资源路径 404。"
        "请确认该硬件组件是否实际存在，或 URI 是否正确"
    ),
    "RedfishException": (
        "[诊断] RedfishException — Redfish API 调用失败，详见 HTTP 状态码和响应体"
    ),
    "AttributeError": (
        "[诊断] AttributeError — 脚本内部对象属性访问错误。"
        "常见原因：① SDK 返回的模型缺少某字段 ② 变量未初始化就使用（如 self.client 为 None）"
        " ③ 上一步操作失败但未中断，导致后续步骤引用了 None 对象"
    ),
    "KeyError": (
        "[诊断] KeyError — 字典 key 不存在。"
        "常见原因：① 配置文件缺少必填字段 ② SDK 响应体缺少预期字段 ③ 枚举 key 大小写不一致"
    ),
    "TimeoutExpired": (
        "[诊断] subprocess.TimeoutExpired — ipmitool 命令执行超时。"
        "常见原因：① BMC 无响应 ② IPMI over LAN 端口未开启 ③ 超时阈值配置过短"
    ),
    "JSONDecodeError": (
        "[诊断] JSONDecodeError — 配置文件 JSON 格式有误，请检查 JSON 语法"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────────────────

def attach_sdk_logger(
    log_file: str,
    logger_name: str = "bmc_test",
    sdk_logger_names: Optional[list[str]] = None,
) -> logging.Logger:
    """初始化测试 logger 并接入 redfish_sdk HTTP 层的调试日志。

    调用后效果：
      - 创建名为 ``logger_name`` 的 Logger，输出到 ``log_file`` 和 stdout
      - ``redfish_sdk.http_client`` logger 的 GET/POST/PATCH/DELETE 请求日志
        （URL、payload、HTTP 状态码）自动写入相同的 log_file
      - 日志级别：文件 DEBUG（记录全量）；stdout INFO（仅重要信息）

    Parameters
    ----------
    log_file : str
        日志文件绝对路径（无需提前创建，目录须存在）
    logger_name : str
        主 Logger 的名称，建议与 LOG_BASE_NAME 保持一致
    sdk_logger_names : list[str], optional
        额外需要接管的 logger 名称，默认接管 ``redfish_sdk.http_client``

    Returns
    -------
    logging.Logger
        主 Logger 实例（同时已完成 SDK logger 的 handler 挂载）
    """
    if sdk_logger_names is None:
        sdk_logger_names = ["redfish_sdk.http_client", "redfish_sdk"]

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler：DEBUG 级别，记录全量（含所有 HTTP 请求细节）
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # stdout handler：INFO 级别，只输出重要信息
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(fmt)

    # 主 Logger
    main_logger = logging.getLogger(logger_name)
    if not main_logger.handlers:   # 避免重复挂载
        main_logger.setLevel(logging.DEBUG)
        main_logger.addHandler(file_handler)
        main_logger.addHandler(stream_handler)
        main_logger.propagate = False

    # 是否将 SDK HTTP 请求日志（URL / body / 响应体）实时输出到 stdout。
    # 默认仅写入 .log 文件（控制台保持干净）；定位 FAIL 时 export BMC_HTTP_DEBUG=1 重跑，
    # 控制台即逐条显示每次接口调用的 URL 与传入 body，便于快速定位问题。
    http_debug_stdout = os.environ.get("BMC_HTTP_DEBUG", "").lower() in ("1", "true", "yes", "on")

    # SDK HTTP Client Logger — 挂载同一文件 handler，开到 DEBUG 接收 HTTP 请求细节
    for sdk_name in sdk_logger_names:
        sdk_log = logging.getLogger(sdk_name)
        # 文件 handler 按目标文件去重，避免重启后重复
        already = any(
            isinstance(h, logging.FileHandler) and h.baseFilename == file_handler.baseFilename
            for h in sdk_log.handlers
        )
        if not already:
            sdk_log.setLevel(logging.DEBUG)
            sdk_log.addHandler(file_handler)
            # SDK 日志默认不输出到 stdout，避免控制台过于嘈杂（请求细节始终写入 .log 文件）
            sdk_log.propagate = False

        # 可选：BMC_HTTP_DEBUG 开启时，额外把 SDK HTTP DEBUG 日志输出到 stdout（按 stream 去重）
        if http_debug_stdout:
            has_stdout = any(
                isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)
                and getattr(h, "stream", None) is sys.stdout
                for h in sdk_log.handlers
            )
            if not has_stdout:
                http_stdout_handler = logging.StreamHandler(sys.stdout)
                http_stdout_handler.setLevel(logging.DEBUG)
                http_stdout_handler.setFormatter(fmt)
                sdk_log.setLevel(logging.DEBUG)
                sdk_log.addHandler(http_stdout_handler)

    return main_logger


def diagnose(
    exc: Optional[Exception] = None,
    context: str = "",
    status_code: Optional[int] = None,
    response_body: Optional[Any] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """在 WARNING / ERROR 发生时输出初步问题定位提示。

    会依次检查：HTTP 状态码 → 响应体关键词 → 异常类型，输出对应的诊断说明。
    诊断结果同时写入 logger（若传入）并以字符串形式返回，便于集成到日志体系。

    Parameters
    ----------
    exc : Exception, optional
        捕获到的异常对象（用于异常类型匹配）
    context : str
        当前操作描述，如 ``"PATCH /redfish/v1/Chassis/1/Drives"``，
        或 ``"检查 Drives 健康状态"``
    status_code : int, optional
        HTTP 响应状态码（从 RedfishException.status_code 或直接传入）
    response_body : str / dict, optional
        HTTP 响应体（用于关键词匹配），可传 str 或 dict
    logger : logging.Logger, optional
        若提供则通过 logger.warning() 输出诊断；否则通过 print 输出

    Returns
    -------
    str — 所有诊断提示的合并字符串（用于写入 JSON result 或其他记录）
    """
    hints: list[str] = []

    # ── 自动从异常中提取 status_code / response_body ──────────────────────────
    if exc is not None:
        if status_code is None and hasattr(exc, "status_code"):
            status_code = exc.status_code
        if response_body is None:
            if hasattr(exc, "body"):
                response_body = exc.body
            elif hasattr(exc, "args") and exc.args:
                response_body = str(exc.args[-1])

    # ── HTTP 状态码诊断 ───────────────────────────────────────────────────────
    if status_code is not None and status_code in _STATUS_HINTS:
        hints.append(_STATUS_HINTS[status_code])

    # ── 响应体关键词诊断 ──────────────────────────────────────────────────────
    body_str = ""
    if isinstance(response_body, dict):
        import json as _json
        try:
            body_str = _json.dumps(response_body, ensure_ascii=False)
        except Exception:
            body_str = str(response_body)
    elif response_body is not None:
        body_str = str(response_body)

    if body_str:
        for keyword, hint in _BODY_KEYWORD_HINTS:
            if keyword.lower() in body_str.lower():
                hints.append(hint)

    # ── 异常类型诊断 ──────────────────────────────────────────────────────────
    if exc is not None:
        exc_type = type(exc).__name__
        # 精确匹配优先，否则前缀匹配
        hint = _EXCEPTION_HINTS.get(exc_type)
        if hint is None:
            for key, val in _EXCEPTION_HINTS.items():
                if exc_type.startswith(key):
                    hint = val
                    break
        if hint:
            hints.append(hint)

    # ── 无命中时给出通用提示 ──────────────────────────────────────────────────
    if not hints:
        hints.append(
            "[诊断] 未命中已知诊断规则，请查看上方完整请求日志（URL/payload/响应体）进行人工分析"
        )

    # ── 拼接并输出 ────────────────────────────────────────────────────────────
    prefix = f"[{context}] " if context else ""
    output_lines = [f"{prefix}{h}" for h in hints]
    full_output = "\n  ".join(output_lines)

    if logger is not None:
        logger.warning("━━ 诊断报告 ━━\n  %s", full_output)
    else:
        from func.common_function import CommonFunction
        CommonFunction.print_log("WARNING", f"━━ 诊断报告 ━━\n  {full_output}")

    return full_output
