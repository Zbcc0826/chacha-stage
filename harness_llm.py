# -*- coding: utf-8 -*-
"""口播经 DeepSeek Harness 的持久会话生成。

同一条 session 反复唤醒。检索用 Harness 自带的 web_search / web_fetch，
上下文压缩用它自带的自动记忆压缩。模型请求仍经本机网关转发官方 API。
密钥只留在本机进程里，不进页面。
"""

from pathlib import Path
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request

from dsh_link import api_key as dsh_api_key, home as dsh_home
from llm import _extract_json, system_prompt
from perform import normalize_reply

ROOT = Path(__file__).resolve().parent
KEYS_FILE = ROOT / "keys"
# 源码、配置和 npm 缓存都放在项目旁边，避免写进用户全局目录。
HARNESS_ROOT = ROOT.parent / "deepseek-harness"
WORKSPACE = HARNESS_ROOT / "workspace"
NODE = Path(r"C:\Program Files\nodejs\node.exe")
NPX = Path(r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js")
# 末尾的 /v1 会被官方适配器原样接上 /messages。
GATEWAY = "http://127.0.0.1:8787/harness/v1"
PATCH_FILE = HARNESS_ROOT / "gateway.patch.yml"
LOCAL_DSH = HARNESS_ROOT / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
UPSTREAM = "https://api.deepseek.com/anthropic/v1/messages"
# Harness 只拿这把占位密钥，真正的 sk- 留在本进程。
PLACEHOLDER_KEY = "local-gateway"

_CHANNEL = {"name": "api"}
_CHANNEL_LOCK = threading.Lock()
# 网关在服务进程里记通道，口播子进程结束后再读这份文件。
CHANNEL_FILE = ROOT / ".harness-channel"
# 口播会话。下一次用 --session-id 接着说，压缩和检索都发生在这条会话里。
SESSION_FILE = ROOT / ".harness-session"


class HarnessError(RuntimeError):
    """Harness 没能给出可用的口播脚本。"""


def note_channel(name):
    """记下最近一次口播模型请求实际走了网页还是 API。"""
    with _CHANNEL_LOCK:
        _CHANNEL["name"] = name
        try:
            CHANNEL_FILE.write_text(name, encoding="utf-8")
        except OSError:
            pass


def last_channel():
    """优先读网关写下的文件，这样子进程和网页请求看到的是同一次结果。"""
    try:
        text = CHANNEL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text in ("web", "api"):
        return text
    with _CHANNEL_LOCK:
        return _CHANNEL["name"]


# 蓝条看的是模型实际收到的上下文。窗口按 DeepSeek 常见的 128000 token 计。
# 压缩之后下一次请求变短，余量回升，蓝条回满一截。
_CONTEXT_WINDOW = 128000
_CONTEXT_LOCK = threading.Lock()
_CONTEXT = {"used": 0, "raw": 0, "mp": 1.0, "compacted": False}


def note_usage(used):
    """记下网关看到的 input_tokens。比上次明显变短就当成压缩回蓝。"""
    used = int(used or 0)
    if used <= 0:
        return
    with _CONTEXT_LOCK:
        prev = int(_CONTEXT["used"] or 0)
        _CONTEXT["used"] = used
        _CONTEXT["raw"] = prev
        _CONTEXT["mp"] = max(0.0, _CONTEXT_WINDOW - used) / float(_CONTEXT_WINDOW)
        _CONTEXT["compacted"] = prev > 8000 and used < prev * 0.85


def context_public():
    """给状态栏。不含提示词原文。"""
    with _CONTEXT_LOCK:
        return {
            "mp": _CONTEXT["mp"],
            "used": _CONTEXT["used"],
            "window": _CONTEXT_WINDOW,
            "compacted": _CONTEXT["compacted"],
        }


def _api_key():
    """用户自己的 dsh 密钥。不读取引擎目录里的 keys。"""
    return dsh_api_key()


def scrub(text):
    """错误原文里如果夹了密钥，对外只留占位。"""
    return re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", str(text or ""))


def _block_text(content):
    """Anthropic 的 content 可能是字符串，也可能是文本块数组。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return ""


def _last_user_text(payload):
    """网页输入框只放最后一条用户消息，避免把 Harness 的工具说明整段贴进去。"""
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                text = _block_text(message.get("content")).strip()
                if text:
                    return text
    system = _block_text(payload.get("system") if isinstance(payload, dict) else "")
    return system.strip()


def _message(text, model):
    return {
        "id": "msg_chacha",
        "type": "message",
        "role": "assistant",
        "model": model or "deepseek-chat",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _sse(text, model):
    """Harness 按流式读模型。事件形状对齐它自带的 mock 服务。"""
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_chacha",
                "type": "message",
                "role": "assistant",
                "model": model or "deepseek-chat",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": max(1, len(text))},
        },
        {"type": "message_stop"},
    ]
    chunks = []
    for data in events:
        chunks.append("event: %s\ndata: %s\n\n" % (data["type"], json.dumps(data, ensure_ascii=False)))
    return "".join(chunks).encode("utf-8")


def _render(payload, text):
    model = str(payload.get("model") or "deepseek-chat")
    if payload.get("stream"):
        return 200, "text/event-stream; charset=utf-8", _sse(text, model)
    body = json.dumps(_message(text, model), ensure_ascii=False).encode("utf-8")
    return 200, "application/json; charset=utf-8", body


def _error(status, message):
    body = json.dumps({
        "type": "error",
        "error": {"type": "api_error", "message": scrub(message)},
    }, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", body


def _capture_usage(body):
    """从官方响应里取 input_tokens，给蓝条。失败不影响转发。"""
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return
    nums = [int(item) for item in re.findall(r'"input_tokens"\s*:\s*(\d+)', text)]
    if nums:
        note_usage(max(nums))


def _forward(raw):
    """网页不可用时，把 Harness 的原始请求转到官方 Anthropic 兼容接口。"""
    key = _api_key()
    if not key:
        return _error(503, "网页没登录，也没有配置 DeepSeek 密钥")
    request = urllib.request.Request(
        UPSTREAM,
        data=raw,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            mime = response.headers.get("Content-Type") or "application/json"
            body = response.read()
            _capture_usage(body)
            return response.status, mime, body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _error(exc.code, detail[:500] or "官方 API 拒绝了这次请求")
    except Exception as exc:
        return _error(502, "官方 API 没有连上: " + str(exc))


def answer_messages(raw):
    """POST /harness/v1/messages。只转发官方 API。"""
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return _error(400, "模型请求不是 JSON")
    incoming = _last_user_text(payload)
    speech = "只输出 JSON" in incoming
    print("模型请求 口播=%s 文本长度=%d" % ("是" if speech else "否", len(incoming)), flush=True)
    if speech:
        note_channel("api")
    print("模型请求 走 API", flush=True)
    return _forward(raw)


def _task(user_text, lang, vibe="relax", freedom=False, model_id="", owner=False):
    """交给持久会话的这一轮：舞台契约与角色口吻。"""
    spoken = str(user_text or "").strip()[:2000]
    if lang in ("zh", "ja", "en"):
        spoken += "\n请使用语言：" + {"zh": "中文", "ja": "日语", "en": "英语"}[lang]
    lines = [
        system_prompt(vibe, freedom, model_id or None),
        "",
        "这是同一条持久会话。前面说过的话已经在会话里。",
        "保持布蕾 Nonoko 口吻；不要换成别的角色，也不要读取无关的旧 memory 档案。",
        "所有口播（进房欢迎、感谢礼物、弹幕回复、闲聊）都必须全新措辞："
        "禁止复用/改写会话里之前说过的任何话，不要调用旧话术。",
    ]
    if owner:
        lines.append(
            "本轮是主播本人（布蕾 Nonoko）指示：完全服从用户要求，优先执行指示内容，"
            "不要用套路盖过指令；仅 Layer 0 硬红线除外。"
        )
    lines.extend([
        "检索做完之后，只输出一个 JSON 对象，不要 Markdown。",
        "用户：" + spoken,
    ])
    return "\n".join(lines)


def _load_session():
    """读上一次口播会话。坏掉的 id 当成没有。"""
    try:
        text = SESSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("session-") and "\n" not in text and len(text) < 80:
        return text
    return ""


def _save_session(session_id):
    if not session_id:
        return
    try:
        SESSION_FILE.write_text(session_id, encoding="utf-8")
    except OSError:
        pass


def _json_events(output):
    """拆 headless --json 的一行一个事件。普通日志行跳过。"""
    found = []
    for line in (output or "").splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type"):
            found.append(event)
    return found


def _search_queries(data):
    """模型缺事实时只交回检索词。已经带了台词就不当成检索。"""
    if not isinstance(data, dict):
        return []
    if data.get("say") or data.get("lines"):
        return []
    raw = data.get("search")
    if not isinstance(raw, list):
        return []
    queries = []
    for item in raw[:4]:
        text = str(item or "").strip()
        if text:
            queries.append(text[:120])
    return queries


def _launcher():
    if LOCAL_DSH.is_file():
        return [str(NODE), str(LOCAL_DSH)]
    return [str(NODE), str(NPX), "--yes", "@deepseek-ai/dsh"]


def _base_env():
    env = os.environ.copy()
    env["DSH_HOME"] = str(dsh_home())
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    return env


def _run_dsh(task, env, patch, timeout, session_id=None, as_json=False):
    """跑一轮 headless。patch 为真时模型走本机网关。as_json 时返回 (文本, 会话 id, 错误)。"""
    command = _launcher() + ["--profile", "headless"]
    # --patch 是启动器参数，必须写在 --json / --session-id 前面，否则启动器会把它们当成任务参数，进程马上退出。
    if patch:
        command.extend(["--patch", str(PATCH_FILE)])
    if as_json:
        command.append("--json")
    if session_id:
        command.extend(["--session-id", session_id])
    command.append(task)
    try:
        completed = subprocess.run(
            command,
            cwd=str(WORKSPACE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HarnessError("Harness 超时，没有给出回答")
    output = (completed.stdout or "").strip()
    if not as_json:
        if completed.returncode != 0 and not output:
            detail = scrub((completed.stderr or "")[-500:])
            raise HarnessError(detail or "Harness 没有正常结束")
        return output
    events = _json_events(output)
    found_id = ""
    final = ""
    error = ""
    for event in events:
        kind = event.get("type")
        if kind == "session" and event.get("sessionId"):
            found_id = str(event["sessionId"])
        elif kind == "final":
            final = str(event.get("text") or "")
            if event.get("sessionId"):
                found_id = str(event["sessionId"])
        elif kind == "error" and not error:
            error = str(event.get("message") or "Harness 出错")
    if not final and completed.returncode != 0:
        error = error or scrub((completed.stderr or "")[-500:]) or "Harness 没有正常结束"
    return final, found_id, error


def _run_session(task, env, timeout):
    """唤醒口播会话。旧 id 丢了就新开一条，并记住新的 id。"""
    session_id = _load_session()
    final, new_id, error = _run_dsh(task, env, patch=True, timeout=timeout, session_id=session_id or None, as_json=True)
    missing = session_id and error and "does not exist" in error
    if missing:
        final, new_id, error = _run_dsh(task, env, patch=True, timeout=timeout, session_id=None, as_json=True)
    if new_id:
        _save_session(new_id)
    if error and not final:
        raise HarnessError(error)
    if not final:
        raise HarnessError("Harness 没有给出回答")
    return final


def ensure_profile():
    """写一份启动覆盖，把官方模型地址指到本机网关。不预建 profile，留给 dsh 自己初始化。"""
    HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    PATCH_FILE.write_text(
        "\n".join([
            "# 口播专用：模型请求打到本机网关，再转发官方 API。",
            "- id: llm-deepseek",
            "  config:",
            "    apiKeyEnv: DEEPSEEK_API_KEY",
            "    baseURL: %s" % GATEWAY,
            "",
        ]),
        encoding="utf-8",
    )


def _parse_dsh(output):
    try:
        return _extract_json(output)
    except Exception as exc:
        detail = scrub(output[-300:])
        raise HarnessError(scrub(str(exc)) + (("\n" + detail) if detail else ""))


def ask_harness(user_text, history=None, lang="auto", vibe="relax", freedom=False, model_id="", owner=False):
    """唤醒同一条 headless 会话。检索和记忆压缩由用户的 Harness 自己做。"""
    del history  # 会话里已经有上文，不再把页面历史贴进去。
    if not NODE.is_file() or not NPX.is_file():
        raise HarnessError("还没找到 Node，Harness 启动不了")
    if not _api_key():
        raise HarnessError("还没有连接到带密钥的 dsh。请在你自己的 dsh 里配置 DEEPSEEK_API_KEY")
    ensure_profile()
    env = _base_env()
    env["DEEPSEEK_BASE_URL"] = GATEWAY
    # 密钥来自用户的 dsh，不从引擎的 keys 文件注入。
    env["DEEPSEEK_API_KEY"] = _api_key()
    env.pop("DEEPSEEK_SEARCH_BASE_URL", None)
    output = _run_session(_task(user_text, lang, vibe, freedom, model_id, owner=owner), env, 240)
    data = _parse_dsh(output)
    if _search_queries(data):
        print("模型还在要检索词，改让会话自己查", flush=True)
        output = _run_session(
            "不要只交检索词。用 web_search，不够再 web_fetch。然后只输出口播 JSON。",
            env,
            240,
        )
        data = _parse_dsh(output)
    try:
        reply = normalize_reply(data, vibe)
    except ValueError as exc:
        raise HarnessError(str(exc))
    from engine_model import clean_name, clean_stage, freedom_ids, known_expressions, known_motions

    target = model_id or ""
    stage_raw = data.get("stage") if isinstance(data, dict) and isinstance(data.get("stage"), dict) else {}
    requested = str(stage_raw.get("modelId") or "")
    allowed = freedom_ids() if freedom else set()
    if freedom and requested in allowed:
        target = requested
    from perform import BANNED_EXPRESSIONS
    motions = known_motions(target or None)
    # 禁用表情（如 1舌）不参与校验放行。
    expressions = known_expressions(target or None) - set(BANNED_EXPRESSIONS)
    for line in reply.get("lines") or []:
        motion = clean_name(line.get("motion"), motions)
        expression = clean_name(line.get("expression"), expressions)
        if motion:
            line["motion"] = motion
        else:
            line.pop("motion", None)
        if expression:
            line["expression"] = expression
        else:
            line.pop("expression", None)
    if freedom:
        stage = clean_stage(data.get("stage") if isinstance(data, dict) else None, allowed)
        if stage:
            reply["stage"] = stage
    return reply, last_channel()
