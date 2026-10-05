"""体检：报告大模型分析链路是否适配当前程序与机器。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\check_report_llm.py
    .venv\\Scripts\\python.exe scripts\\check_report_llm.py --record outputs\\runtime_data\\live_rally_record.json

依次检查六件事，任何一步失败都会给出下一步该怎么做：
  1. 应用实际配置（provider / endpoint / model）—— 从注册表读，不猜
  2. 本地服务是否真的在监听（裸 socket，避免代理干扰）
  3. 可用模型列表，以及配置里的模型是否已拉取
  4. 请求体积与上下文占用是否适配（本地小上下文最容易踩的坑）
  5. 代理是否劫持了 localhost
  6. 真实调用一次，报告耗时与抽取到的文本

只读，不修改任何配置或文件。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib import parse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.report_generation import (  # noqa: E402
    OpenAICompatibleReportApiClient,
    ReportGenerationRequest,
    ReportGenerationService,
    _API_MAX_OUTPUT_TOKENS,
    _API_RECORD_CHAR_BUDGET,
    _is_loopback_host,
    _strip_reasoning,
)

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"
INFO = "[INFO]"

problems: list[str] = []
next_steps: list[str] = []


def say(mark: str, text: str) -> None:
    print(f"{mark} {text}")


def section(title: str) -> None:
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def est_tokens(text: str) -> int:
    """粗估 token：CJK 约 1 字 1 token，其余约 4 字符 1 token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + max(1, (len(text) - cjk) // 4)


# ---------------------------------------------------------------- 1. 配置
def read_app_config() -> dict[str, Any]:
    section("1. 应用实际配置（来自 QSettings 注册表）")
    config: dict[str, Any] = {}
    try:
        import winreg

        path = r"Software\WFBARNet\PyQt6Runtime"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        for index in range(winreg.QueryInfoKey(key)[1]):
            name, value, _ = winreg.EnumValue(key, index)
            if name.startswith("report_api"):
                config[name] = value
        winreg.CloseKey(key)
    except FileNotFoundError:
        say(WARN, "注册表里没有 WFBARNet 配置，说明程序还没保存过报告设置。")
    except Exception as exc:  # pragma: no cover - 环境相关
        say(WARN, f"读取注册表失败：{exc}")

    if not config:
        say(INFO, "改用代码默认值。")
    for name in sorted(config):
        value = config[name]
        if "key" in name.lower():
            value = "(已设置)" if value else "(空)"
        say(INFO, f"  {name} = {value}")
    return config


# ------------------------------------------------------- 2/3. 服务与模型
def check_service(endpoint: str) -> tuple[bool, str, list[str]]:
    section("2/3. 本地服务与可用模型")
    try:
        parsed = parse.urlsplit(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        say(FAIL, f"endpoint 无法解析：{endpoint}")
        problems.append("endpoint 配置不合法")
        return False, "", []

    if not _is_loopback_host(host):
        say(INFO, f"{host}:{port} 不是回环地址（云端 API），跳过本地服务探测。")
        return True, host, []

    sock = socket.socket()
    sock.settimeout(3.0)
    try:
        sock.connect((host, port))
        say(OK, f"{host}:{port} 端口已监听。")
        listening = True
    except Exception as exc:
        say(FAIL, f"{host}:{port} 连不上（{type(exc).__name__}: {exc}）。")
        listening = False
        problems.append("本地推理服务没有在运行")
        next_steps.append(
            "启动本地模型服务（Ollama：安装后运行 `ollama serve`，"
            "或直接运行 `ollama list` 会自动拉起后台服务）。"
        )
    finally:
        sock.close()

    models: list[str] = []
    if listening:
        from urllib import request as urlrequest

        # 显式绕开代理，否则会被劫持成 502
        opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
        for api in ("/api/tags", "/v1/models"):
            url = f"{parse.urlunsplit((parsed.scheme, parsed.netloc, api, '', ''))}"
            try:
                with opener.open(url, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                raw = data.get("models") or []
                models = [
                    str(item.get("name") or item.get("id") or "")
                    for item in raw
                    if isinstance(item, dict)
                ]
                if models:
                    say(OK, f"{api} 返回 {len(models)} 个模型：")
                    for name in models:
                        say(INFO, f"    - {name}")
                    break
            except Exception as exc:
                say(WARN, f"{api} 读取失败：{type(exc).__name__}: {exc}")
        if not models:
            say(WARN, "没有读到任何模型，可能还没拉取模型。")
            next_steps.append("拉取模型，例如 `ollama pull qwen3:8b`。")
    return listening, host, models


# ------------------------------------------------------ 4. 上下文是否适配
def check_payload_fit(record: dict[str, Any] | None, model: str) -> None:
    section("4. 请求体积与上下文适配")
    if record is None:
        say(WARN, "没有可用的真实记录（跑一次分析后会生成），跳过体积检查。")
        next_steps.append("先用程序分析一段视频，生成 outputs/runtime_data/live_rally_record.json。")
        return

    request = ReportGenerationRequest.from_rally_record(record, athlete_name="检查", video_name="probe.mp4")
    payload = request.to_payload()
    sent = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    hits = (record.get("details") or {}).get("hits") or []
    say(INFO, f"原始击球事件：{len(hits)} 条")
    info = ((payload.get("data") or {}).get("rally_record") or {}).get("details", {}).get("events_info")
    if isinstance(info, dict):
        say(INFO, f"发送前压缩：total={info.get('total')} included={info.get('included')} sampled={info.get('sampled')}")

    input_tokens = est_tokens(sent)
    system_tokens = 175
    total = system_tokens + input_tokens + _API_MAX_OUTPUT_TOKENS
    say(INFO, f"发给模型的 JSON：{len(sent)} 字符，约 {input_tokens} tokens")
    say(INFO, f"估算总占用：系统提示 {system_tokens} + 输入 {input_tokens} + 输出上限 {_API_MAX_OUTPUT_TOKENS} = {total} tokens")

    if total <= 4096:
        say(OK, "可放进 4096 上下文（Ollama 默认值），有富余。")
    elif total <= 8192:
        say(WARN, f"需要 8192 上下文才能完整容纳（当前约 {total}）。")
        problems.append("请求超过 4096 上下文")
        next_steps.append(
            "把模型的上下文调大：新建 Modelfile 写 `PARAMETER num_ctx 8192`，"
            "`ollama create qwen3-8b-8k -f Modelfile`，再把程序里的模型名改成它。"
        )
    else:
        say(WARN, f"约 {total} tokens，超出常见本地上下文，可能被截断。")

    if len(sent) > _API_RECORD_CHAR_BUDGET + 2000:
        say(WARN, "压缩后仍偏大，检查 compact_rally_record_for_api 的预算。")
    say(INFO, f"（配置的模型：{model or '(空)'}）")


# ------------------------------------------------------------- 5. 代理
def check_proxy(endpoint: str) -> None:
    section("5. localhost 是否被代理劫持")
    import os

    proxies = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
    if not proxies:
        say(OK, "环境里没有代理变量。")
    else:
        for name, value in sorted(proxies.items()):
            say(WARN, f"  {name} = {value}")
        try:
            host = parse.urlsplit(endpoint).hostname or ""
        except ValueError:
            host = ""
        if _is_loopback_host(host):
            say(OK, "endpoint 是回环地址，程序会绕开代理（_url_opener 已处理）。")
        else:
            say(INFO, "endpoint 不是回环地址，会按正常代理规则走。")


# ------------------------------------------------------------ 6. 真实调用
def check_live_call(endpoint: str, model: str, api_key: str, record: dict[str, Any] | None) -> None:
    section("6. 真实调用一次")
    if not endpoint or not model:
        say(WARN, "endpoint 或 model 为空，跳过。")
        return
    if record is None:
        say(WARN, "没有真实记录，改用最小样本调用。")
        record = {"summary": {"rally_hit_count": 3, "duration_s": 10.0}, "details": {"hits": [], "players": {}}}

    request = ReportGenerationRequest.from_rally_record(record, athlete_name="检查", video_name="probe.mp4")
    client = OpenAICompatibleReportApiClient(endpoint, model=model, api_key=api_key, timeout_s=180.0)
    started = time.perf_counter()
    try:
        raw = client.generate(request)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        say(FAIL, f"{elapsed:.1f}s 后失败：{type(exc).__name__}: {exc}")
        problems.append("真实调用失败")
        message = str(exc)
        if "10061" in message or "积极拒绝" in message:
            next_steps.append("服务没在监听：先确认本地模型服务已启动。")
        elif "502" in message or "Bad Gateway" in message:
            next_steps.append("502 通常意味着代理劫持；确认程序用的是绕开代理的版本。")
        elif "timed out" in message.lower():
            next_steps.append("超时：模型可能正在加载，或显存不足换更小的模型。")
        return

    elapsed = time.perf_counter() - started
    text = str(raw.get("report_text") or "")
    say(OK, f"{elapsed:.1f}s 调用成功。")
    say(INFO, f"provider={raw.get('provider')}  status={raw.get('status')}")
    say(INFO, f"report_text：{len(text)} 字符")
    if text:
        preview = text[:300].replace("\n", " ")
        say(INFO, f"  预览：{preview}")
        if "＜think" in text or "<" + "think" in text:
            say(FAIL, "文本里仍含思考块标记，剥离逻辑需要检查。")
            problems.append("思考块未被剥离")
    else:
        say(WARN, "文本为空：模型没有返回可用内容。")
        next_steps.append("确认模型名与已拉取的一致，并检查是否被输出上限截断。")

    # 端到端：LLM 文本是否真的进了渲染后的报告
    service = ReportGenerationService(client)
    try:
        response = service.generate(request, use_api=True)
    except Exception as exc:
        say(WARN, f"端到端渲染失败：{exc}")
        return
    source = response.report_data.get("analysis_source")
    say(INFO, f"报告标注 analysis_source = {source}")
    if text.strip() and source == "llm":
        say(OK, "大模型文本已注入报告，并标注为「大模型分析」。")
    elif not text.strip():
        say(WARN, "模型没产出文本，报告会退回「本地规则分析」。")
    else:
        say(WARN, "标注不是 llm，检查 _inject_report_text 的调用路径。")


def load_record(path: str | None) -> dict[str, Any] | None:
    candidate = Path(path) if path else PROJECT_ROOT / "outputs" / "runtime_data" / "live_rally_record.json"
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return None
    record = data.get("rally_record") if isinstance(data, dict) else None
    if isinstance(record, dict):
        print(f"{INFO} 使用记录：{candidate}")
    return record if isinstance(record, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="报告大模型链路适配自检")
    parser.add_argument("--record", default=None, help="rally_record JSON（默认用 outputs/runtime_data/live_rally_record.json）")
    args = parser.parse_args()

    print("WFBARNet 报告大模型链路 —— 适配自检")
    print(f"项目根目录：{PROJECT_ROOT}")

    config = read_app_config()
    endpoint = str(config.get("report_api_endpoint") or "http://127.0.0.1:11434/v1/chat/completions")
    model = str(config.get("report_api_model") or "qwen3:8b")
    api_key = str(config.get("report_api_key") or "")
    enabled = bool(config.get("report_api_enabled", True))

    if not enabled:
        say(WARN, "程序里「启用大模型报告分析」是关闭的，报告只会用本地规则分析。")
        problems.append("大模型分析开关未开启")
        next_steps.append("在程序设置里打开「启用大模型报告分析」。")

    record = load_record(args.record)
    listening, _host, models = check_service(endpoint)
    check_payload_fit(record, model)
    check_proxy(endpoint)

    if listening and models and model and not any(m == model or m.split(":")[0] == model.split(":")[0] for m in models):
        say(WARN, f"配置的模型 {model} 不在已拉取列表里。")
        problems.append(f"模型 {model} 未拉取")
        next_steps.append(f"执行 `ollama pull {model}`，或在程序设置里改成已存在的模型。")

    check_live_call(endpoint, model, api_key, record)

    section("结论")
    if not problems:
        say(OK, "没有发现问题：配置、服务、体积、代理、调用、端到端渲染都正常。")
    else:
        for item in problems:
            say(FAIL, item)
        print()
        say(INFO, "下一步：")
        for step in dict.fromkeys(next_steps):
            say(INFO, f"  - {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
