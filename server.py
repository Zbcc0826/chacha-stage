# -*- coding: utf-8 -*-
"""虚拟主播 / 桌宠引擎。

页面和运行时来自本地目录。内置模型是大肥鱼。
口播由用户自己的 dsh 发起，本进程不读取 keys 文件。
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from danmaku import feed, redact, save_live_key
from dsh_link import api_key as dsh_api_key, home as dsh_home, key_ready, save_api_key, save_home, status as dsh_status
from engine_model import (
    browse,
    ensure_index,
    freedom_ids,
    load_library,
    model_map,
    picker_label,
    public_catalog,
    public_model,
    remove_model,
    save_model,
    set_freedom,
)
from harness_llm import HarnessError, answer_messages, ask_harness, context_public
from perform import catalog, load_vibe, plan, public_vibe, save_vibe
from tts import synthesize

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VENDOR = ROOT / "vendor"
ensure_index()
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
    """已保存的模型。路径留在本机页面上，方便确认选的是哪一套。"""
    return [public_model(record) for record in load_library()]


def public_error(exc):
    """错误信息可以回给页面，但不能带上密钥或直播身份码。"""
    text = redact(str(exc)).replace("\n", " ")
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
    published = []
    try:
        try:
            script, channel = ask_harness(
                user_text,
                payload.get("history"),
                payload.get("lang") or "auto",
                vibe,
                payload.get("freedom") is True,
                str(payload.get("model") or ""),
            )
        except HarnessError as exc:
            raise RuntimeError(str(exc))
        speak = payload.get("speak", True) is not False
        joined = []
        for index, line in enumerate(script["lines"]):
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
            elif index == 0:
                performance["vibe"] = public_vibe(script["vibe"])
            if line.get("motion"):
                performance["motion"] = line["motion"]
            if line.get("expression"):
                performance["expression"] = line["expression"]
            if index == 0 and script.get("stage"):
                performance["stage"] = script["stage"]
            if index == 0 and heard:
                performance["heard"] = heard
                performance["danmaku"] = feed.status()
            performance["lineIndex"] = index
            performance["lineCount"] = len(script["lines"])
            published.append(publish_act(performance))
            joined.append(performance.get("say") or line["say"])
            print("已交出第 %d/%d 句" % (index + 1, len(script["lines"])), flush=True)
        save_vibe(script["endVibe"])
        say = "\n".join(item for item in joined if item)
        if heard:
            feed.remember("danmaku", heard["msg"], say, heard.get("uname") or "")
        elif user_text:
            feed.remember("ask", user_text, say, "你")
        # 句子已经逐句交出去了。这里只回报文本，避免整包再播一遍。
        return {
            "streamed": True,
            "idle": False,
            "say": say,
            "actIds": [item.get("actId") for item in published],
            "lineCount": len(published),
            "vibe": public_vibe(script["vibe"]),
            "reply": script,
            "channel": channel,
            "via": "harness",
            "stage": script.get("stage"),
            "heard": heard,
            "danmaku": feed.status() if heard else None,
        }
    except Exception as exc:
        print("口播失败:", public_error(exc), flush=True)
        if claimed and not published:
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


def reset_balance():
    """换了密钥就丢掉上一轮余额，下一轮血条重新问官方接口。"""
    _BALANCE.update(at=0.0, hp=None, yuan=None, error="")


def save_dsh_settings(payload):
    """保存 dsh 目录或用户自己的密钥。响应里不带密钥。"""
    if any(name in payload for name in ("apiKey", "api_key", "token")):
        raise RuntimeError("密钥请填在页面的密钥框里")
    home_text = str(payload.get("home") or "").strip()
    has_key = "key" in payload
    if not home_text and not has_key:
        raise RuntimeError("没有要保存的内容")
    folder = save_home(home_text) if home_text else dsh_home()
    if has_key:
        save_api_key(payload.get("key") or "")
        reset_balance()
    return {"ok": True, "home": str(folder), "dsh": dsh_status()}


def balance_hp():
    """查官方余额并换成血量。页面上看不到密钥。"""
    now = time.time()
    if _BALANCE["at"] and now - _BALANCE["at"] < _BALANCE_TTL:
        return {"hp": _BALANCE["hp"], "yuan": _BALANCE["yuan"], "error": _BALANCE["error"]}
    key = dsh_api_key()
    if not key.startswith("sk-"):
        _BALANCE.update(at=now, hp=None, yuan=None, error="还没有密钥，官方余额查不了")
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
# 最近几句口播。桌宠和直播间轮询来取。只留最后一句的话，前面已经合成好的句子会丢。
_ACT_GUARD = threading.Lock()
_ACT_SEQ = 0
_ACT_BODY = None
_ACTS = []


def publish_act(body):
    """给这一句口播编号。空转结果不占号。"""
    global _ACT_SEQ, _ACT_BODY
    if not isinstance(body, dict) or body.get("idle"):
        return body
    with _ACT_GUARD:
        _ACT_SEQ += 1
        stored = dict(body)
        stored["actId"] = _ACT_SEQ
        _ACTS.append(stored)
        del _ACTS[:-16]
        _ACT_BODY = stored
        return stored


def read_act(after):
    """取出还没播过的句子。一句就原样返回，多句按顺序装在 batch 里。"""
    try:
        seen = int(after)
    except (TypeError, ValueError):
        seen = 0
    with _ACT_GUARD:
        pending = [item for item in _ACTS if int(item.get("actId") or 0) > seen]
        if not pending:
            return {"idle": True, "actId": _ACT_SEQ}
        if len(pending) == 1:
            return pending[0]
        return {"batch": pending, "actId": pending[-1]["actId"]}


# 直播间和桌宠不是同一个浏览器，本地存储互不相通。形象和语音开关放在服务里共用一份。
_SHARE_GUARD = threading.Lock()
_LOOK_REV = 0
_LOOK = None
_MIC_REV = 0
_MIC = {"want": False, "hold": [], "owner": "", "ownerAt": 0.0}
_LOOK_TOGGLES = ("physics", "breath", "sway", "blink", "mouse", "ahoge")


def _share_number(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(low, min(high, number))


def _clean_pose(raw):
    """只留下参数名和数值。条数有上限，避免一份超大姿态把轮询撑满。"""
    if not isinstance(raw, dict):
        return {}
    pose = {}
    for key, value in list(raw.items())[:80]:
        name = str(key)[:80]
        try:
            pose[name] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return pose


def _clean_toggles(raw):
    src = raw if isinstance(raw, dict) else {}
    return {name: bool(src.get(name)) for name in _LOOK_TOGGLES}


def _clean_hold(raw):
    if not isinstance(raw, list):
        return []
    hold = []
    for item in raw[:2]:
        name = str(item or "")[:20]
        if name and name not in hold:
            hold.append(name)
    return hold


def _expire_mic_owner(now):
    """桌宠停了报活，把收听权交回直播间，并记一笔让页面能看见。"""
    global _MIC_REV
    if _MIC["owner"] == "pet" and now - float(_MIC["ownerAt"] or 0) > 2.5:
        _MIC["owner"] = ""
        _MIC_REV += 1


def _public_mic(now):
    """调用前要先做过期处理。空着的收听权算直播间。"""
    return {"rev": _MIC_REV, "want": bool(_MIC["want"]), "hold": list(_MIC["hold"]), "owner": _MIC["owner"] or "web"}


def post_share(payload):
    """写入形象或语音。没变化不增加序号，避免两边互相覆盖。"""
    global _LOOK_REV, _LOOK, _MIC_REV
    now = time.time()
    if not isinstance(payload, dict):
        payload = {}
    with _SHARE_GUARD:
        _expire_mic_owner(now)
        look = payload.get("look")
        if isinstance(look, dict):
            body = {
                "modelId": str(look.get("modelId") or "")[:80],
                "zoom": round(_share_number(look.get("zoom"), 0.25, 4.0, 1.0), 3),
                "x": round(_share_number(look.get("x"), -2000, 2000, 0), 1),
                "y": round(_share_number(look.get("y"), -2000, 2000, 0), 1),
                "toggles": _clean_toggles(look.get("toggles")),
                "pose": _clean_pose(look.get("pose")),
            }
            previous = None if _LOOK is None else {key: _LOOK.get(key) for key in body}
            if body != previous:
                _LOOK_REV += 1
                stored = dict(body)
                stored["rev"] = _LOOK_REV
                _LOOK = stored
        mic = payload.get("mic")
        if isinstance(mic, dict):
            changed = False
            if "want" in mic and bool(mic.get("want")) != bool(_MIC["want"]):
                _MIC["want"] = bool(mic.get("want"))
                changed = True
            if "hold" in mic:
                hold = _clean_hold(mic.get("hold"))
                if hold != _MIC["hold"]:
                    _MIC["hold"] = hold
                    changed = True
            owner = str(mic.get("owner") or "")
            # 桌宠还在报活时，直播间不能把收听权抢回去。
            if owner == "web" and _MIC["owner"] == "pet" and now - float(_MIC["ownerAt"] or 0) < 2.5:
                owner = ""
            if owner in ("pet", "web"):
                if _MIC["owner"] != owner:
                    _MIC["owner"] = owner
                    changed = True
                _MIC["ownerAt"] = now
            if changed:
                _MIC_REV += 1
        return {"look": _LOOK, "mic": _public_mic(now)}


def read_share(look_after, mic_after):
    """没有新序号就留空，页面不必反复套用同一份状态。"""
    try:
        seen_look = int(look_after)
    except (TypeError, ValueError):
        seen_look = 0
    try:
        seen_mic = int(mic_after)
    except (TypeError, ValueError):
        seen_mic = 0
    now = time.time()
    with _SHARE_GUARD:
        _expire_mic_owner(now)
        look = _LOOK if _LOOK is not None and _LOOK_REV > seen_look else None
        mic = _public_mic(now) if _MIC_REV > seen_mic else None
        return {"look": look, "mic": mic, "lookRev": _LOOK_REV, "micRev": _MIC_REV}
# 桌宠是单独的透明置顶窗口，不占用这个 HTTP 线程。
# 名字要和 pet_window.py 里的互斥量一致。一台机器只留一个桌宠。
_PET_LOCK = threading.Lock()
_pet_proc = None
_pet_external_pid = None
_pet_log = None
PET_LOG = ROOT / "pet-window.log"
PET_PID = ROOT / "pet-window.pid"
PET_MUTEX_NAME = "Local\\ChachaStageDesktopPet"


def _pet_mutex_held():
    """桌宠进程还占着互斥量，说明窗口已经在。"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenMutexW(0x00100000, 0, PET_MUTEX_NAME)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def _pid_alive(pid):
    if not pid or pid == os.getpid():
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, 0, int(pid))
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def _pet_process_pids():
    """本目录正在跑的桌宠进程。服务重启后靠这个把旧窗口认回来。"""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like $env:CHACHA_PET_CMD } | "
        "ForEach-Object { $_.ProcessId }"
    )
    env = os.environ.copy()
    env["CHACHA_PET_CMD"] = "*" + str(ROOT / "pet_window.py") + "*"
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            env=env,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.SubprocessError, OSError):
        return []
    pids = []
    for line in out.split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return pids


def _stop_pid(pid):
    if not _pid_alive(pid):
        return
    try:
        os.kill(int(pid), 15)
    except OSError:
        return


def _read_pid_file():
    try:
        return int(PET_PID.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def pet_status():
    """桌宠进程还在不在。服务自己拉起的、互斥量占着的、认领的旧窗口都算开着。"""
    proc = _pet_proc
    if proc is not None and proc.poll() is None:
        return {"open": True}
    if _pet_mutex_held() or _pid_alive(_pet_external_pid):
        return {"open": True}
    return {"open": False}


def _claim_pets(pids):
    """留一个，多出来的关掉。优先留服务自己拉起的那个。"""
    global _pet_external_pid
    if not pids:
        return False
    tracked = _pet_proc.pid if _pet_proc is not None and _pet_proc.poll() is None else 0
    if tracked and tracked in pids:
        keep = tracked
    elif _pet_external_pid and int(_pet_external_pid) in pids:
        keep = int(_pet_external_pid)
    else:
        keep = pids[0]
    _pet_external_pid = keep
    for extra in pids:
        if extra != keep:
            _stop_pid(extra)
    return True


def open_pet():
    """打开透明置顶浮窗。已经有一个就接着用，不再开第二个。"""
    global _pet_proc, _pet_log, _pet_external_pid
    with _PET_LOCK:
        if pet_status()["open"]:
            _claim_pets(_pet_process_pids())
            return {"open": True}
        pids = _pet_process_pids()
        if _claim_pets(pids):
            return {"open": True}
        _pet_external_pid = None
        _pet_log = open(PET_LOG, "w", encoding="utf-8")
        _pet_proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "pet_window.py")],
            cwd=str(ROOT),
            stdout=_pet_log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        proc = _pet_proc
    time.sleep(0.8)
    if proc.poll() is not None:
        if _pet_mutex_held() or _pet_process_pids():
            return {"open": True}
        return {"open": False, "error": _pet_failure()}
    return {"open": True}


def close_pet():
    """关掉全部桌宠浮窗。直播间页面留着。"""
    global _pet_proc, _pet_external_pid
    with _PET_LOCK:
        targets = set(_pet_process_pids())
        proc = _pet_proc
        if proc is not None and proc.poll() is None:
            targets.add(proc.pid)
        if _pet_external_pid:
            targets.add(int(_pet_external_pid))
        recorded = _read_pid_file()
        if recorded:
            targets.add(recorded)
        for pid in targets:
            _stop_pid(pid)
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        _pet_proc = None
        _pet_external_pid = None
        if not _pid_alive(_read_pid_file()):
            try:
                PET_PID.unlink()
            except OSError:
                pass
    return {"open": False}


def _pet_failure():
    text = ""
    try:
        text = PET_LOG.read_text(encoding="utf-8", errors="replace")[-400:]
    except OSError:
        text = ""
    print(text)
    if "No module named" in text and "webview" in text:
        return "没有 pywebview，桌宠窗口开不了"
    return "桌宠窗口没有留下来"


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
            self._json({"models": model_catalog(), "picker": picker_label()})
            return
        if path == "/api/catalog":
            body = catalog()
            body.update(public_catalog())
            self._json(body)
            return
        if path == "/api/stage":
            query = parse_qs(parsed.query)
            model_id = (query.get("model") or [""])[0]
            self._json(public_catalog(model_id or None))
            return
        if path == "/api/dsh":
            self._json(dsh_status())
            return
        if path == "/api/pet":
            self._json(pet_status())
            return
        if path == "/api/cue":
            query = parse_qs(parsed.query)
            self._json(read_act((query.get("after") or ["0"])[0]))
            return
        if path == "/api/share":
            query = parse_qs(parsed.query)
            self._json(read_share((query.get("look") or ["0"])[0], (query.get("mic") or ["0"])[0]))
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
            spec = model_map().get(model_id)
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
                self._json(publish_act(build_performance(payload, payload.get("speak", True) is not False)))
            elif path == "/api/chat":
                result = build_chat(payload)
                if isinstance(result, dict) and (result.get("idle") or result.get("streamed")):
                    self._json(result)
                else:
                    self._json(publish_act(result))
            elif path == "/api/dsh":
                try:
                    self._json(save_dsh_settings(payload))
                except RuntimeError as exc:
                    self._json({"error": public_error(exc)}, 400)
            elif path == "/api/web-login":
                self._json({"error": "网页版口播已停用，请使用你自己的 dsh"}, 410)
            elif path == "/api/models/browse":
                self._json(browse())
            elif path == "/api/models/save":
                saved = save_model(payload.get("path") or "", payload.get("name") or "", payload.get("freedom") is True)
                self._json({"ok": True, "model": saved, "models": model_catalog()})
            elif path == "/api/models/freedom":
                updated = set_freedom(str(payload.get("id") or ""), payload.get("freedom") is True)
                self._json({"ok": True, "model": updated, "models": model_catalog()})
            elif path == "/api/models/remove":
                remove_model(str(payload.get("id") or ""))
                self._json({"ok": True, "models": model_catalog()})
            elif path == "/api/pet":
                self._json(open_pet() if payload.get("open") is True else close_pet())
            elif path == "/api/share":
                self._json(post_share(payload))
            elif path == "/api/danmaku/done":
                feed.finish(payload.get("id"))
                self._json({"ok": True, "danmaku": feed.status()})
            elif path == "/api/bili":
                if "key" not in payload:
                    raise RuntimeError("没有要保存的内容")
                save_live_key(payload.get("key") or "")
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
    _claim_pets(_pet_process_pids())
    danmaku = feed.status()
    print("弹幕        %s" % danmaku["hint"])
    httpd.serve_forever()


if __name__ == "__main__":
    main()
