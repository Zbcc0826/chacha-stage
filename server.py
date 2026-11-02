# -*- coding: utf-8 -*-
"""虚拟主播 / 桌宠引擎。

页面和运行时来自本地目录。内置模型是大肥鱼。
口播由用户自己的 dsh 发起，本进程不读取 keys 文件。
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import json
import re
import threading
import time
import urllib.request
import uuid

from danmaku import feed
from dsh_link import api_key as dsh_api_key, key_ready, save_home, status as dsh_status
from engine_model import ensure_index, public_catalog, spec as fish_spec
from harness_llm import HarnessError, answer_messages, ask_harness, context_public
from perform import catalog, load_vibe, plan, public_vibe, save_vibe
from tts import synthesize

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VENDOR = ROOT / "vendor"
ensure_index()
_fish = fish_spec()
# 只内置大肥鱼。目录在本地，接口不回磁盘路径。
MODELS = {
    _fish["id"]: _fish,
}
AUDIO_DIR = ROOT / "audio"
HOST = "127.0.0.1"
PORT = 8787
_AUDIO_NAME = re.compile(r"^[a-f0-9]{32}\.mp3$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".moc3": "application/octet-stream",
    ".mp3": "audio/mpeg",
}


def model_catalog() -> list:
    """可供切换的模型。ready 只表示入口 json 是否存在。"""
    items = []
    for model_id, spec in MODELS.items():
        items.append({
            "id": model_id,
            "name": spec["name"],
            "mark": spec["mark"],
            "model": spec["model"],
            "cdi": spec["cdi"],
            "ready": (spec["dir"] / spec["model"]).is_file(),
        })
    return items


def public_error(exc):
    """错误信息可以回给页面，但不能带上密钥。"""
    text = str(exc).replace("\n", " ")
    if "sk-" in text:
        return "上游请求失败"
    return text[:300] or "请求失败"


def save_audio(data):
    """把合成结果放到 audio 目录，只保留最近 30 条。"""
    AUDIO_DIR.mkdir(exist_ok=True)
    name = uuid.uuid4().hex + ".mp3"
    (AUDIO_DIR / name).write_bytes(data)
    files = sorted(AUDIO_DIR.glob("*.mp3"), key=lambda item: item.stat().st_mtime)
    for old in files[:-30]:
        old.unlink(missing_ok=True)
    return "/audio/" + name


def build_performance(payload, with_audio):
    """先排动作，再合成声音。声音失败时仍然返回动作时间轴。"""
    script = plan(
        payload.get("text") or payload.get("say") or "",
        mood=payload.get("mood"),
        cues=payload.get("cues"),
        lang=payload.get("lang"),
        voice=payload.get("voice"),
        pitch=payload.get("pitch"),
        rate=payload.get("rate"),
    )
    script["audio"] = None
    if with_audio and not script["say"]:
        script["audioError"] = "这句话里没有要读的字"
    elif with_audio and script["say"]:
        try:
            audio = synthesize(script["say"], script["voice"], rate=script["rate"], pitch=script["pitch"])
            if audio:
                script["audio"] = save_audio(audio)
            else:
                script["audioError"] = "语音是空的"
        except Exception as exc:
            script["audioError"] = public_error(exc)
            print("语音失败:", script["audioError"], flush=True)
    return script


def build_chat(payload):
    """让 Harness 写一轮口播。多句按顺序合成，整体动态从上一次接着。

    live 为真且没有手打内容时，先挑一条弹幕再生成。没有可回的弹幕就空转，不叫模型。
    """
    live = payload.get("live") is True
    user_text = str(payload.get("text") or "").strip()
    claimed = None
    heard = None
    held = False
    if live and not user_text:
        if not _GEN_LOCK.acquire(blocking=False):
            return {"idle": True, "danmaku": feed.status()}
        held = True
        claimed = feed.claim()
        if claimed is None:
            _GEN_LOCK.release()
            return {"idle": True, "danmaku": feed.status()}
        heard = {"id": claimed["id"], "uname": claimed["uname"], "msg": claimed["msg"]}
        user_text = feed.prompt_for(claimed)
    else:
        _GEN_LOCK.acquire()
        held = True
    if not user_text:
        if held:
            _GEN_LOCK.release()
        raise RuntimeError("没有要问的内容")
    vibe = load_vibe()
    try:
        try:
            script, channel = ask_harness(
                user_text,
                payload.get("history"),
                payload.get("lang") or "auto",
                vibe,
                payload.get("freedom") is True,
            )
        except HarnessError as exc:
            raise RuntimeError(str(exc))
        speak = payload.get("speak", True) is not False
        lines = []
        for line in script["lines"]:
            performance = build_performance({
                "text": line["say"],
                "mood": line.get("mood"),
                "cues": line.get("cues"),
                "lang": payload.get("lang"),
                "voice": payload.get("voice"),
                "pitch": payload.get("pitch"),
                "rate": payload.get("rate"),
            }, with_audio=speak)
            if line.get("vibe"):
                performance["vibe"] = public_vibe(line["vibe"])
            if line.get("motion"):
                performance["motion"] = line["motion"]
            if line.get("expression"):
                performance["expression"] = line["expression"]
            lines.append(performance)
        save_vibe(script["endVibe"])
        # 顶层保留第一句，旧页面至少能播出开头；新页面按 lines 依次播。
        combined = dict(lines[0])
        combined["lines"] = lines
        combined["say"] = "\n".join(item["say"] for item in lines)
        combined["vibe"] = public_vibe(script["vibe"])
        combined["reply"] = script
        combined["channel"] = channel
        combined["via"] = "harness"
        if script.get("stage"):
            combined["stage"] = script["stage"]
        if heard:
            combined["heard"] = heard
            combined["danmaku"] = feed.status()
            feed.remember("danmaku", heard["msg"], combined.get("say") or "", heard.get("uname") or "")
        elif user_text:
            feed.remember("ask", user_text, combined.get("say") or "", "你")
        return combined
    except Exception as exc:
        print("口播失败:", public_error(exc), flush=True)
        if claimed:
            feed.release(claimed)
        raise
    finally:
        if held:
            _GEN_LOCK.release()


def llm_ready() -> bool:
    """用户的 dsh 是否已带上密钥。不返回密钥内容。"""
    return key_ready()


# 余额查询别打太勤。100 元是满血，再多也按满血。
_BALANCE = {"at": 0.0, "hp": None, "yuan": None, "error": ""}
_BALANCE_TTL = 30


def balance_hp():
    """查官方余额并换成血量。页面上看不到密钥。"""
    now = time.time()
    if _BALANCE["at"] and now - _BALANCE["at"] < _BALANCE_TTL:
        return {"hp": _BALANCE["hp"], "yuan": _BALANCE["yuan"], "error": _BALANCE["error"]}
    key = dsh_api_key()
    if not key.startswith("sk-"):
        _BALANCE.update(at=now, hp=None, yuan=None, error="dsh 还没有密钥")
        return {"hp": None, "yuan": None, "error": _BALANCE["error"]}
    request = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        yuan = None
        for item in payload.get("balance_infos") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("currency") or "").upper() != "CNY":
                continue
            yuan = float(item.get("total_balance") or 0)
            break
        if yuan is None:
            raise RuntimeError("没有人民币余额")
        hp = max(0.0, min(1.0, yuan / 100.0))
        _BALANCE.update(at=now, hp=hp, yuan=round(yuan, 2), error="")
    except Exception as exc:
        _BALANCE.update(at=now, hp=_BALANCE["hp"], yuan=_BALANCE["yuan"], error=public_error(exc))
    return {"hp": _BALANCE["hp"], "yuan": _BALANCE["yuan"], "error": _BALANCE["error"]}


def hud_status():
    """血条是余额，蓝条是上下文余量。"""
    money = balance_hp()
    mind = context_public()
    return {
        "hp": money["hp"],
        "yuan": money["yuan"],
        "hpError": money["error"],
        "mp": mind["mp"],
        "used": mind["used"],
        "window": mind["window"],
        "compacted": mind["compacted"],
    }


def safe_join(root: Path, rel: str) -> Path | None:
    """把 URL 相对路径限制在 root 内，拒绝目录穿越。"""
    rel = rel.lstrip("/").replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None
    return target


# 同一时间只跑一轮口播生成。第二轮如果是弹幕轮询，直接空转，避免两段一起说。
_GEN_LOCK = threading.Lock()


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/keys", "/keys/", "/api/keys"):
            self.send_error(404)
            return
        if path == "/api/status":
            self._json({
                "models": model_catalog(),
                "llm": "deepseek",
                "llmReady": llm_ready(),
                "dsh": dsh_status(),
                "vibe": public_vibe(load_vibe()),
                "danmaku": feed.status(),
            })
            return
        if path == "/api/hud":
            self._json(hud_status())
            return
        if path == "/api/models":
            self._json({"models": model_catalog()})
            return
        if path == "/api/catalog":
            body = catalog()
            body.update(public_catalog())
            self._json(body)
            return
        if path == "/api/stage":
            self._json(public_catalog())
            return
        if path == "/api/dsh":
            self._json(dsh_status())
            return
        if path == "/api/prompt":
            from llm import system_prompt
            body = system_prompt(load_vibe()).encode("utf-8")
            self._bytes(body, "text/plain; charset=utf-8")
            return
        if path.startswith("/audio/"):
            name = path[len("/audio/"):]
            if not _AUDIO_NAME.match(name):
                self.send_error(404)
                return
            self._file(AUDIO_DIR / name)
            return
        if path in ("/", "/index.html"):
            self._file(WEB / "index.html")
            return
        if path.startswith("/vendor/"):
            target = safe_join(VENDOR, path[len("/vendor/") :])
            self._file(target)
            return
        if path == "/harness/v1/models":
            self._json({"object": "list", "data": [
                {"id": "deepseek-chat", "object": "model"},
                {"id": "deepseek-flash", "object": "model"},
            ]})
            return
        if path.startswith("/models/"):
            rest = path[len("/models/") :]
            model_id, _, rel = rest.partition("/")
            spec = MODELS.get(model_id)
            if spec is None or not rel:
                self.send_error(404)
                return
            self._file(safe_join(spec["dir"], rel))
            return
        target = safe_join(WEB, path)
        self._file(target)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        length = int(self.headers.get("Content-Length") or 0)
        # Harness 的代理请求会带上工具说明，比口播接口大。
        limit = 8_000_000 if path.startswith("/harness/") else 1_000_000
        if length > limit:
            self._json({"error": "请求太大"}, 413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        if path == "/harness/v1/messages":
            status, mime, body = answer_messages(raw)
            self._bytes(body, mime, status)
            return
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json({"error": "JSON 无法解析"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"error": "需要一个 JSON 对象"}, 400)
            return
        try:
            if path == "/api/perform":
                self._json(build_performance(payload, payload.get("speak", True) is not False))
            elif path == "/api/chat":
                self._json(build_chat(payload))
            elif path == "/api/dsh":
                if any(name in payload for name in ("key", "apiKey", "api_key", "token")):
                    self._json({"error": "不要把密钥发给引擎"}, 400)
                    return
                folder = save_home(payload.get("home") or "")
                self._json({"ok": True, "home": str(folder), "dsh": dsh_status()})
            elif path == "/api/web-login":
                self._json({"error": "网页版口播已停用，请使用你自己的 dsh"}, 410)
            elif path == "/api/danmaku/done":
                feed.finish(payload.get("id"))
                self._json({"ok": True, "danmaku": feed.status()})
            else:
                self.send_error(404)
        except Exception as exc:
            self._json({"error": public_error(exc)}, 500)

    def _file(self, target: Path | None):
        if target is None or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        mime = MIME.get(target.suffix.lower(), "application/octet-stream")
        self._bytes(data, mime)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data: bytes, mime: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # 不打印请求里可能出现的敏感查询。
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    ready = [item["name"] for item in model_catalog() if item["ready"]]
    if not ready:
        raise SystemExit("没有可用的 Live2D 模型")
    httpd = Server((HOST, PORT), Handler)
    print("大肥鱼引擎  http://%s:%d/" % (HOST, PORT))
    print("模型        %s" % "、".join(ready))
    print("dsh         %s" % ("已连接" if llm_ready() else "未配置密钥"))
    print("口播通道    用户的 dsh")
    feed.start()
    danmaku = feed.status()
    print("弹幕        %s" % danmaku["hint"])
    httpd.serve_forever()


if __name__ == "__main__":
    main()
