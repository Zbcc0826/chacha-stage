# -*- coding: utf-8 -*-
"""从弹幕事件流收直播弹幕，并在开口前挑下一条。

默认走 bbq.remoon.cn。身份码按密钥填在页面上，只写进 bili.local 的 key。
连接时才放进上游要求的参数，不进页面，也不写进日志。
红线弹幕在进入队列之前丢掉，不会交给口播生成。
"""

from pathlib import Path
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
BILI_FILE = ROOT / "bili.local"
# 托管的弹幕事件流。查询参数只在连接时拼上，不出现在页面和日志里。
DEFAULT_SSE = "https://bbq.remoon.cn/bili-api/danmu"
# 保存身份码后叫醒正在等待的连接线程。
_WAKE = threading.Event()

# 这些词出现在弹幕里就整条丢掉。只用于过滤，不会写进口播提示。
_RED_WORDS = (
    "习近平", "习主席", "共产党", "国民党", "民进党", "台独", "港独", "藏独",
    "六四", "天安门", "法轮功", "政治局", "国务院", "选举", "总统", "总理",
    "颜色革命", "政治", "支那", "nmsl", "你妈", "去死", "杀了你", "脑残", "智障",
    "系统提示", "提示词", "忽略以上", "忽略之前", "忽略上面",
    "约炮", "裸聊", "色情", "做爱", "强奸", "性侵", "幼女", "幼童", "萝莉", "正太",
)

# 话题重合时忽略的口头语，避免「这个」「哈哈」把无关弹幕抬成当前话题。
_STOP_GRAMS = {
    "这个", "那个", "什么", "怎么", "就是", "不是", "我们", "你们", "他们",
    "一个", "一下", "哈哈", "啊啊", "今天", "现在", "可以", "知道", "觉得",
    "观众", "直播", "弹幕", "还是", "真的", "没有", "自己", "因为", "所以",
}

# 记忆里的梗，按子串匹配。老吴和意气风发分开，不拼成一条。
_MEME_WORDS = (
    "哈基米", "南北绿豆", "曼波", "叮咚鸡", "大狗叫", "老吴", "意气风发",
    "杰哥不要", "你很勇嘛", "啊对对对", "有内鬼", "终止交易", "保熟",
    "蚌埠住了", "绷不住", "破防", "典中典", "那我问你", "显眼包", "尊嘟假嘟",
    "泰裤辣", "家人们谁懂", "针不戳", "爷青回", "前方高能", "下次一定",
    "好家伙", "不会吧", "逆天", "属于是", "无语子", "雨女无瓜", "芭比q",
    "你人还怪好",
)

_DANMU_CMDS = ("LIVE_OPEN_PLATFORM_DM", "LIVE_OPEN_PLATFORM_DM_MIRROR")
_CHAR = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def load_bili():
    """读取 bili.local。缺字段就留空，不把内容打到日志。"""
    cfg = {"idcode": "", "sse": DEFAULT_SSE, "key": "", "secret": "", "appid": ""}
    if not BILI_FILE.is_file():
        return cfg
    for line in BILI_FILE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        name = key.strip().lower()
        if name in cfg:
            cfg[name] = value.strip()
    if not cfg["sse"]:
        cfg["sse"] = DEFAULT_SSE
    return cfg


def live_key(cfg=None):
    """本机保存的直播身份码。调用方不要把它写进页面或日志。"""
    data = cfg if cfg is not None else load_bili()
    return (data.get("key") or data.get("idcode") or "").strip()


def save_live_key(value):
    """按密钥把身份码写进 bili.local。空字符串会清掉旧的 idcode 和 key。"""
    text = str(value or "").strip()
    if any(ch in text for ch in "\r\n\x00"):
        raise RuntimeError("身份码里不能换行")
    kept = []
    if BILI_FILE.is_file():
        for line in BILI_FILE.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                kept.append(line)
                continue
            name = raw.split("=", 1)[0].strip().lower()
            if name in ("key", "idcode"):
                continue
            kept.append(line)
    else:
        kept.append("# 本地弹幕配置。不要提交。网页和日志都不回显这些值。")
        kept.append("sse=" + DEFAULT_SSE)
    if text:
        kept.append("key=" + text)
    BILI_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    _WAKE.set()


def redact(text):
    """从一段报错里去掉身份码和地址。"""
    secret = live_key()
    cleaned = str(text or "")
    if secret:
        cleaned = cleaned.replace(secret, "")
        cleaned = cleaned.replace(urllib.parse.quote(secret), "")
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    return cleaned


def stream_url(cfg):
    """拼事件流地址。身份码只在这一刻放进上游要求的参数。"""
    base = (cfg.get("sse") or DEFAULT_SSE).strip()
    lowered = base.lower()
    if "idcode=" in lowered or "key=" in lowered:
        return base
    sep = "&" if "?" in base else "?"
    return base + sep + "IDCode=" + urllib.parse.quote(live_key(cfg))


def _quiet_error(exc, secret):
    """异常原文里可能夹着地址，去掉后再打日志。"""
    text = redact(exc)
    if secret:
        text = text.replace(secret, "")
    text = re.sub(r"\s+", " ", text).strip()
    return (type(exc).__name__ + (" " + text[:80] if text else "")).strip()


def is_redline(text):
    """红线为真时，调用方必须当没看见，不能送进模型。"""
    folded = re.sub(r"\s+", "", str(text or "")).lower()
    if not folded:
        return False
    return any(word.lower() in folded for word in _RED_WORDS)


def has_meme(text):
    """弹幕是否带记忆里的梗。"""
    folded = re.sub(r"\s+", "", str(text or "")).lower()
    return any(word in folded for word in _MEME_WORDS)


def _grams(text):
    chars = _CHAR.findall(str(text or ""))
    found = set()
    for size in (2, 3):
        for index in range(0, len(chars) - size + 1):
            found.add("".join(chars[index:index + size]))
    return found - _STOP_GRAMS


def topic_overlap(msg, topic):
    """和当前话题共用的词片数量。没有话题时是 0。"""
    if not topic or not msg:
        return 0
    return len(_grams(msg) & _grams(topic))


def _clean(text, limit):
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    return raw[:limit]


# 同时堆着这么多还没挑的弹幕，就只挑一条进回复队列。
_MANY = 3


class DanmakuFeed:
    """后台连着弹幕机，开口时再从队列里挑一条。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._board = []
        self._next_id = 1
        self._seen = []
        self._topic = None
        self.connected = False
        self._thread = None
        # 走系统代理。空代理会把外网事件流也绕开。
        self._opener = urllib.request.build_opener()

    def start(self):
        """只启动一次。连不上就隔几秒重试，不把地址打出来。"""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="bili-danmaku", daemon=True)
        self._thread.start()

    def remember(self, origin, text, say="", uname=""):
        """记下话题是哪条弹幕带起来的。红线不记。她自己的话只作附注，不当新开头。"""
        clean = _clean(text, 180)
        if not clean or is_redline(clean) or origin not in ("danmaku", "ask", "self"):
            return
        said = _clean(say, 80)
        if said and is_redline(said):
            said = ""
        name = _clean(uname, 24) or "观众"
        with self._lock:
            self._topic = {"origin": origin, "text": clean, "say": said, "uname": name}

    def claim(self):
        """挑下一条来回。人多时只留最好玩的一条，其余不进回复队列。"""
        with self._lock:
            self._drop_redline()
            self._fill_reply_slot()
            chosen = next((item for item in self._board if item["state"] == "queued"), None)
            if chosen is None:
                fresh = [item for item in self._board if item["state"] == "new"]
                if not fresh:
                    return None
                chosen = self._pick(fresh)
                if len(fresh) >= _MANY:
                    for item in fresh:
                        if item is not chosen:
                            item["state"] = "skipped"
            for item in self._board:
                if item is not chosen and item["state"] == "speaking":
                    item["state"] = "done"
            chosen["state"] = "speaking"
            return chosen

    def release(self, item):
        """生成失败时放回排队。同一条失败两次就丢掉，免得堵住后面的人。"""
        if not item:
            return
        item["fails"] = int(item.get("fails") or 0) + 1
        with self._lock:
            if item["fails"] >= 2:
                item["state"] = "skipped"
                return
            item["state"] = "queued"

    def finish(self, item_id):
        """这句说完了，金边让给下一条。"""
        with self._lock:
            for item in self._board:
                if item["id"] == item_id and item["state"] == "speaking":
                    item["state"] = "done"
                    return True
        return False

    def prompt_for(self, item):
        """把挑中的弹幕写成这一轮的用户消息。没人接的旧话题不写进去。"""
        name = item.get("uname") or "观众"
        topic = self._topic or {}
        if item.get("follow") and topic.get("text"):
            who = topic.get("uname") or "观众"
            return (
                "这是直播间里的一条弹幕，要说给观众听。\n"
                "观众「%s」说：%s\n"
                "这句还在接「%s」发起的话题：%s\n"
                "顺着这个话题接。有梗就用梗接，别解释梗从哪来。"
                "没写在这里的弹幕当没看见，也不要说自己跳过了什么。"
                % (name, item["msg"], who, topic.get("text"))
            )
        return (
            "这是直播间里的一条弹幕，要说给观众听。\n"
            "观众「%s」说：%s\n"
            "这是新的一句，不要接着刚才没人接的话继续说。"
            "有梗就用梗接，别解释梗从哪来。没写在这里的弹幕当没看见。"
            % (name, item["msg"])
        )

    def status(self):
        """给页面的连接状态，以及气泡要画的弹幕。不含身份码和密钥。"""
        cfg = load_bili()
        with self._lock:
            pending = sum(1 for item in self._board if item["state"] in ("new", "queued", "speaking"))
            board = [
                {
                    "id": item["id"],
                    "uname": item["uname"],
                    "msg": item["msg"],
                    "state": item["state"],
                }
                for item in self._board[-24:]
            ]
            connected = self.connected
        if not live_key(cfg):
            hint = "还没有直播身份码"
        elif not connected:
            hint = "正在连接弹幕"
        else:
            hint = "弹幕已接上"
        return {
            "configured": bool(live_key(cfg)),
            "connected": connected,
            "pending": pending,
            "hint": hint,
            "board": board,
        }

    def _drop_redline(self):
        """已经进板的红线改成跳过，不再拿去回复。"""
        for item in self._board:
            if item["state"] in ("new", "queued") and is_redline(item["msg"]):
                item["state"] = "skipped"

    def _fill_reply_slot(self):
        """人多时只留一条排队。后来若明显更好玩，换掉正在排的那条。"""
        pool = [item for item in self._board if item["state"] in ("new", "queued")]
        if len(pool) >= _MANY:
            chosen = self._pick(pool)
            for item in pool:
                item["state"] = "queued" if item is chosen else "skipped"
            return
        queued = [item for item in pool if item["state"] == "queued"]
        fresh = [item for item in pool if item["state"] == "new"]
        if not queued or not fresh:
            return
        winner = self._pick(queued + fresh)
        if winner not in fresh:
            return
        if self._fun(winner["msg"]) < self._fun(queued[0]["msg"]) + 2 and not (
            has_meme(winner["msg"]) and not has_meme(queued[0]["msg"])
        ):
            return
        for item in queued:
            item["state"] = "skipped"
        winner["state"] = "queued"

    def _fun(self, text):
        """粗分好不好玩。梗和问句优先，太短的灌水靠后。"""
        raw = str(text or "")
        score = 0
        if has_meme(raw):
            score += 5
        if "？" in raw or "?" in raw:
            score += 2
        if 4 <= len(raw) <= 40:
            score += 1
        if len(raw) <= 1:
            score -= 2
        return score

    def _pick(self, pool):
        """有明显更好玩的就选它。都差不多就随机一条，不把整池都排进回复。"""
        topic = self._topic or {}
        origin = topic.get("origin") or ""
        blob = ((topic.get("text") or "") + (topic.get("say") or "")) if origin else ""
        scored = []
        for item in pool:
            overlap = topic_overlap(item["msg"], blob) if blob else 0
            scored.append((item, overlap))
        if origin == "self" and not any(overlap for _item, overlap in scored):
            self._topic = None
            blob = ""
            origin = ""
        ranked = []
        for item, overlap in scored:
            fun = self._fun(item["msg"]) + (3 if overlap and blob else 0)
            ranked.append((item, fun, overlap, has_meme(item["msg"])))
        ranked.sort(key=lambda row: (-row[1], row[0]["ts"], row[0]["id"]))
        best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else best[1] - 3
        if len(ranked) == 1 or best[1] >= second + 2 or best[3]:
            chosen = best[0]
            overlap = best[2]
        else:
            band = [row for row in ranked if best[1] - row[1] <= 1]
            picked = random.choice(band)
            chosen = picked[0]
            overlap = picked[2]
        chosen["follow"] = bool(overlap) and origin in ("danmaku", "ask", "self")
        if origin in ("danmaku", "ask") and not overlap:
            # 这条没接上一个话题，下一句不要再硬续。
            self._topic = None
        return chosen

    def _trim_board(self):
        """板子只留最近几十条，正在回和排队的不删。"""
        while len(self._board) > 40:
            drop_at = None
            for state in ("done", "skipped", "new"):
                for index, item in enumerate(self._board):
                    if item["state"] == state:
                        drop_at = index
                        break
                if drop_at is not None:
                    break
            if drop_at is None:
                return
            self._board.pop(drop_at)

    def _loop(self):
        told = False
        while True:
            _WAKE.clear()
            cfg = load_bili()
            secret = live_key(cfg)
            if not secret:
                self.connected = False
                _WAKE.wait(5)
                continue
            url = stream_url(cfg)
            try:
                request = urllib.request.Request(
                    url,
                    headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
                )
                with self._opener.open(request, timeout=25) as response:
                    self.connected = True
                    told = False
                    print("弹幕已连上", flush=True)
                    self._consume(response)
            except Exception as exc:
                self.connected = False
                if not told:
                    print("弹幕还没连上:", _quiet_error(exc, secret), flush=True)
                    told = True
                _WAKE.wait(3)
            finally:
                self.connected = False

    def _consume(self, response):
        data_lines = []
        while True:
            raw = response.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    self._on_event("\n".join(data_lines))
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    def _on_event(self, payload):
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict) or msg.get("type") != "danmu":
            return
        body = msg.get("data") if isinstance(msg.get("data"), dict) else {}
        if body.get("cmd") not in _DANMU_CMDS:
            return
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        self._offer(data.get("msg_id"), data.get("uname"), data.get("msg"), data.get("timestamp"))

    def _offer(self, msg_id, uname, msg, stamp=None):
        """收一条弹幕。红线直接丢，不进队列。"""
        text = _clean(msg, 180)
        if not text:
            return
        if is_redline(text):
            return
        name = _clean(uname, 24)
        if not name or is_redline(name):
            name = "观众"
        key = "%s\n%s\n%s\n%s" % (msg_id or "", name, text, stamp or "")
        with self._lock:
            if key in self._seen:
                return
            self._seen.append(key)
            if len(self._seen) > 400:
                del self._seen[:200]
            self._board.append({
                "id": self._next_id,
                "seq": key,
                "uname": name,
                "msg": text,
                "ts": time.time(),
                "fails": 0,
                "state": "new",
                "follow": False,
            })
            self._next_id += 1
            self._trim_board()
            self._fill_reply_slot()


feed = DanmakuFeed()
