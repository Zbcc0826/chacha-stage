# -*- coding: utf-8 -*-
"""从弹幕事件流收直播弹幕，并在开口前挑下一条。

默认走 bbq.remoon.cn，只带身份码。身份码放在 bili.local，不进页面，也不写进日志。
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
# 礼物要优先感谢；进房单独欢迎，不能跟普通弹幕混成一句聊天。
_GIFT_CMDS = ("LIVE_OPEN_PLATFORM_SEND_GIFT",)
_ENTER_CMDS = ("LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER",)
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


def stream_url(cfg):
    """拼事件流地址。身份码只放在查询参数里。"""
    base = (cfg.get("sse") or DEFAULT_SSE).strip()
    if "IDCode=" in base:
        return base
    sep = "&" if "?" in base else "?"
    return base + sep + "IDCode=" + urllib.parse.quote(cfg["idcode"])


def _quiet_error(exc, secret):
    """异常原文里可能夹着地址，去掉后再打日志。"""
    text = str(exc)
    if secret:
        text = text.replace(secret, "")
    text = re.sub(r"https?://\S+", "", text)
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


def is_fake_gift_claim(text):
    """普通弹幕是否在口头假装送礼（并非开放平台礼物事件）。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    folded = re.sub(r"\s+", "", raw)
    # 「怎么送礼」「想送礼」这类询问不算装送。
    if any(q in folded for q in ("怎么送", "如何送", "怎样送", "想送", "要不要送", "礼物在哪", "怎么上舰")):
        return False
    if _FAKE_GIFT_CLAIM.search(folded):
        return True
    # 「我送你舰长」一类：有送/刷 + 礼物名词。
    if ("送" in folded or "刷" in folded) and any(word in folded for word in _FAKE_GIFT_NOUN):
        return True
    return False


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


def is_nonoko_name(name):
    """昵称是否像主播本人（布蕾 Nonoko / 蕾蕾 / 蕾宝）。"""
    folded = re.sub(r"\s+", "", str(name or ""))
    if not folded:
        return False
    if "nonoko" in folded.lower():
        return True
    for key in ("布蕾Nonoko", "布蕾", "蕾蕾", "蕾宝"):
        if key in folded:
            return True
    return False


def owner_obey_block():
    """主播本人指示时的最高优先级规则。"""
    return (
        "【主播本人指令 · 最高优先级】\n"
        "发话人是布蕾 Nonoko 本人（或控制台主播指示）。必须完全服从本轮指示的内容、语气、话题与动作要求。\n"
        "不要反驳、不要擅自改主题、不要用弱智吧/要礼物/闲聊套路盖过她的指令。\n"
        "仍可保持布蕾口吻念出来；若指示要求某种说法或动作，优先照做（motion/expression 仍只能用模型已有 id）。\n"
        "仅 Layer 0 硬红线（涉政涉黄涉暴、医疗诊断等）除外，其余一律服从。\n"
    )


def pronoun_block(uname="观众"):
    """弹幕人称必须分清：弹幕里的「我」=观众，「你」=主播。"""
    name = _clean(uname, 24) or "观众"
    return (
        "【人称必须分清 · 很重要】\n"
        "弹幕是「%s」说的。读弹幕时：弹幕里的「我/我们」= 这位观众本人；弹幕里的「你/你们」= 主播布蕾 Nonoko（你自己）。\n"
        "你口播时：第一人称「我」永远是主播布蕾；称呼对方用「你」或点名「%s」。\n"
        "不要把观众说的「我」理解成主播，也不要把观众说的「你」理解成观众自己。\n"
        % (name, name)
    )


def _as_int(value, fallback=0):
    """礼物价格、数量、粉丝牌等级都可能是字符串。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _fans_level(data):
    """开放平台 fans_medal_level：等级越高说明打钱越多，要更重视。"""
    if not isinstance(data, dict):
        return 0
    level = _as_int(
        data.get("fans_medal_level")
        if data.get("fans_medal_level") is not None
        else data.get("fans_level"),
        0,
    )
    if level:
        return max(0, level)
    # 有的事件把粉丝牌塞在 medal / fans_medal 里。
    for key in ("fans_medal", "medal", "medal_info"):
        nested = data.get(key)
        if isinstance(nested, dict):
            nested_level = _as_int(
                nested.get("level")
                if nested.get("level") is not None
                else nested.get("medal_level"),
                0,
            )
            if nested_level:
                return max(0, nested_level)
    return 0


def _uface(data):
    """开放平台用户头像 URL（字段 uface）。只收 http(s)，避免脏数据。"""
    if not isinstance(data, dict):
        return ""
    raw = str(
        data.get("uface")
        or data.get("face")
        or data.get("avatar")
        or data.get("user_face")
        or ""
    ).strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return ""
    return raw[:500]


def _fans_hint(level):
    """写进口播提示：高等级 = 大客户，低等级也不要冷落。"""
    level = int(level or 0)
    if level <= 0:
        return "这条没有粉丝牌等级（或为 0），正常互动即可。"
    if level >= 21:
        return "粉丝牌等级 %d，非常高，说明打钱很多，要格外重视、更热情，可温柔提舰长/盲盒但别逼单。" % level
    if level >= 11:
        return "粉丝牌等级 %d，偏高，说明打赏不少，回复更用心一点，可自然邀继续上舰或盲盒。" % level
    if level >= 5:
        return "粉丝牌等级 %d，有一定投入，友好互动，见缝插针可提舰长/盲盒。" % level
    return "粉丝牌等级 %d，有牌就记得点到，别忽略老粉感。" % level


def _norm_cmd(cmd):
    return str(cmd or "").strip().upper()


def _event_parts(msg):
    """从几种常见包装里取出 cmd 和业务数据。身份码不会出现在这里。"""
    node = msg
    raw = node.get("data")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            node = parsed
    cmd = _norm_cmd(node.get("cmd") or msg.get("cmd") or node.get("type") or msg.get("type"))
    inner = node.get("data") if isinstance(node.get("data"), dict) else None
    if inner is None and isinstance(msg.get("data"), dict) and node is not msg:
        inner = msg.get("data")
    if isinstance(inner, dict) and inner.get("cmd"):
        cmd = _norm_cmd(inner.get("cmd"))
        nested = inner.get("data")
        data = nested if isinstance(nested, dict) else inner
    elif isinstance(inner, dict):
        data = inner
    else:
        data = node if isinstance(node, dict) else {}
    return cmd, data if isinstance(data, dict) else {}


def _person_name(data):
    """进房/礼物的昵称字段不统一，挨个试。"""
    if not isinstance(data, dict):
        return ""
    for key in ("uname", "username", "user_name", "nickname", "nick_name", "name"):
        name = _clean(data.get(key), 24)
        if name:
            return name
    info = data.get("user") or data.get("user_info") or data.get("sender")
    if isinstance(info, dict):
        return _person_name(info)
    return ""


# 同一观众 30 分钟内再次进房不重复欢迎；超过 30 分钟则欢迎回家。
_ENTER_GAP = 30 * 60
# 过去 1 分钟内有过非进房弹幕/礼物，或板上还有待回聊天时，跳过进房欢迎。
_ENTER_QUIET = 60
_VISIT_KEEP = 7 * 24 * 3600
_VISIT_FILE = ROOT / "enter_visits.json"
# 板上最多留多少条；优先删已完成/已跳过，再删续聊分最低的旧弹幕。
_BOARD_KEEP = 80
# 明显灌水/签到类，续聊分压低。
_FILLER_MSGS = {
    "666", "6666", "66666", "哈哈哈", "哈哈哈哈", "哈哈", "呵呵", "嘿嘿",
    "来了", "打卡", "签到", "前排", "沙发", "地板", "路过", "支持", "加油",
    "111", "222", "啊啊啊", "哦哦哦", "不错", "好好好",
}
# 自己闲聊里软邀礼物：最快隔 10 分钟一次。
_GIFT_ASK_GAP = 10 * 60
_GIFT_ASK_WORDS = ("舰长", "上舰", "盲盒", "送礼", "打赏", "电池", "投喂", "上船")
# 普通弹幕里假装送礼的用语（不是 LIVE_OPEN_PLATFORM_SEND_GIFT 事件）。
_FAKE_GIFT_CLAIM = re.compile(
    r"(送了|送给你|送你|我送|给你送|送出|刷了|打赏了|投喂了|上了舰|上舰了|上了个舰|"
    r"开了舰|开舰长|送舰长|送提督|送总督|送盲盒|送花花|送小花|送电池|送辣条|"
    r"礼物到账|礼物到了|我刷礼物|给你刷)"
)
_FAKE_GIFT_NOUN = (
    "礼物", "舰长", "提督", "总督", "盲盒", "花花", "小花花", "电池", "辣条",
    "嘉年华", "礼花", "气球", "上舰", "打赏", "投喂",
)
# 滚动上下文：最近若干条弹幕/闲聊 + 自己的回复，供理解与续聊。
_CONTEXT_KEEP = 10


def _load_visits():
    """读进房记录。文件坏了就当没人来过。"""
    if not _VISIT_FILE.is_file():
        return {}
    try:
        raw = json.loads(_VISIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    visits = {}
    for name, stamp in raw.items():
        try:
            visits[str(name)] = float(stamp)
        except (TypeError, ValueError):
            continue
    return visits


def _save_visits(visits):
    """只留最近 7 天，避免昵称文件一直变大。"""
    now = time.time()
    kept = {name: stamp for name, stamp in visits.items() if now - stamp <= _VISIT_KEEP}
    try:
        _VISIT_FILE.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return
    visits.clear()
    visits.update(kept)


class DanmakuFeed:
    """后台连着弹幕机，开口时再从队列里挑一条。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._board = []
        self._next_id = 1
        self._seen = []
        # 已回复过的弹幕正文指纹，同一句内容只回一遍。
        self._replied = []
        self._topic = None
        self.connected = False
        self._thread = None
        # 走系统代理。空代理会把外网事件流也绕开。
        self._opener = urllib.request.build_opener()
        # uname -> 上次进房时间。用来判断 30 分钟内是否还要欢迎。
        self._visits = _load_visits()
        # 最近口播台词（欢迎/谢礼/弹幕/闲聊），禁止下一轮照抄会话旧话。
        self._recent_says = []
        # 上次闲聊要礼物的时间戳；弹幕借机要礼物不走这个冷却。
        self._last_gift_ask = 0.0
        # 近期弹幕/闲聊与回复成对上下文，最多 _CONTEXT_KEEP 条。
        self._context = []
        # 上次非进房事件时间：用来判断房间是否安静到可以欢迎进房。
        self._last_non_enter_ts = 0.0

    def can_filler_gift_ask(self):
        """闲聊是否已过 10 分钟冷却，可以软邀一次礼物。"""
        return (time.time() - float(self._last_gift_ask or 0)) >= _GIFT_ASK_GAP

    def note_filler_gift_ask(self, say=""):
        """记下闲聊要礼物：显式调用，或台词里出现舰长/盲盒等词。"""
        text = str(say or "")
        hit = (not text) or any(word in text for word in _GIFT_ASK_WORDS)
        if not hit:
            return False
        self._last_gift_ask = time.time()
        return True

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

    def drop_self_topic(self):
        """回完弹幕后丢掉自己闲聊话题，避免继续刚才那套闲聊。"""
        with self._lock:
            if self._topic and self._topic.get("origin") == "self":
                self._topic = None

    def note_context(self, kind, uname, msg, say):
        """记下一条「对方说了什么 + 我回了什么」，滚动保留约 10 条。"""
        user = _clean(msg, 120)
        reply = _clean(say, 160)
        if not user and not reply:
            return
        row = {
            "kind": kind or "danmaku",
            "uname": _clean(uname, 24) or "观众",
            "msg": user,
            "say": reply,
        }
        with self._lock:
            self._context.append(row)
            if len(self._context) > _CONTEXT_KEEP:
                del self._context[: len(self._context) - _CONTEXT_KEEP]

    def clear_context(self):
        """开播刷新时清空滚动上下文。"""
        with self._lock:
            self._context = []

    def context_rows(self):
        """复制近期上下文，供拼提示用。"""
        with self._lock:
            return [dict(item) for item in self._context[-_CONTEXT_KEEP:]]

    def context_block(self, title="近期直播上下文（弹幕/闲聊与你的回复，最多10条）"):
        """写成模型可读的上下文块；没有则空串。"""
        rows = self.context_rows()
        if not rows:
            return ""
        lines = [
            title + "：",
            "人称：历史弹幕里的「我」=当时那位观众，「你」=主播；你自己的回复里「我」=主播。",
            "请结合这些内容理解语境；优先回本轮新内容，可自然关联旧梗，禁止复读旧台词。",
        ]
        for index, item in enumerate(rows, 1):
            kind = item.get("kind") or "danmaku"
            name = item.get("uname") or "观众"
            msg = item.get("msg") or ""
            say = item.get("say") or ""
            if kind == "gift":
                who = "礼物·观众%s" % name
            elif kind == "enter":
                who = "进房·观众%s" % name
            elif kind == "filler":
                who = "主播闲聊"
            elif kind == "ask":
                who = "主播指示"
            else:
                who = "观众%s的弹幕" % name
            bit = "%d）%s" % (index, who)
            if msg:
                bit += "说「%s」" % msg
            if say:
                bit += " → 主播回「%s」" % say
            lines.append(bit)
        return "\n".join(lines) + "\n"

    def _content_key(self, item):
        """同一观众同一句话视为同一内容。礼物按消息号分开，避免连送被当成重复跳过。"""
        kind = item.get("kind") or "danmaku"
        if kind == "gift":
            return "gift\n%s\n%s\n%s" % (
                item.get("uname") or "",
                item.get("gift_name") or "",
                item.get("msg_id") or item.get("seq") or "",
            )
        if kind == "enter":
            # 同一个人再次进房也要再谢一次，只按消息号去重。
            return "enter\n%s\n%s" % (item.get("uname") or "", item.get("msg_id") or item.get("seq") or "")
        return "%s\n%s" % (item.get("uname") or "", item.get("msg") or "")

    def _mark_replied(self, item):
        key = self._content_key(item)
        if not key.strip() or key in self._replied:
            return
        self._replied.append(key)
        if len(self._replied) > 400:
            del self._replied[:200]

    def discard_pending(self):
        """刷新/开播时丢掉板上还没回的旧弹幕，只接这之后新来的。"""
        with self._lock:
            for item in self._board:
                if item["state"] in ("new", "queued", "speaking"):
                    item["state"] = "skipped"
            # 旧话题也不带到新一轮，避免硬续刷新前的话。
            self._topic = None
            self._context = []
        return self.status()

    def clear_visits(self):
        """清空进房 uname 记录；之后再进都当第一次欢迎。"""
        with self._lock:
            count = len(self._visits)
            self._visits.clear()
        try:
            if _VISIT_FILE.is_file():
                _VISIT_FILE.unlink()
        except OSError:
            try:
                _VISIT_FILE.write_text("{}", encoding="utf-8")
            except OSError:
                pass
        print("已清除进房记录：%d 人" % count, flush=True)
        return {"ok": True, "cleared": count, "danmaku": self.status()}

    def claim(self):
        """挑下一条来回：续聊分高的优先；其余留在序列里稍后一一回复。"""
        with self._lock:
            self._drop_redline()
            self._drop_busy_enters()
            # 已回过的正文直接跳过，避免同内容再播一遍。
            for item in self._board:
                if item["state"] in ("new", "queued") and self._content_key(item) in self._replied:
                    item["state"] = "skipped"
            self._fill_reply_slot()
            chosen = next((item for item in self._board if item["state"] == "queued"), None)
            if chosen is None:
                fresh = [item for item in self._board if item["state"] == "new"]
                if not fresh:
                    return None
                chosen = self._pick(fresh)
            for item in self._board:
                if item is not chosen and item["state"] == "speaking":
                    item["state"] = "done"
            chosen["state"] = "speaking"
            return chosen

    def note_replied(self, item_id):
        """口播已成功生成后调用：同内容不再进入待回。"""
        with self._lock:
            for item in self._board:
                if item["id"] == item_id:
                    self._mark_replied(item)
                    return True
        return False

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
                if item["id"] == item_id and item["state"] in ("speaking", "queued", "new"):
                    item["state"] = "done"
                    self._mark_replied(item)
                    return True
        return False

    def prompt_for(self, item):
        """把挑中的弹幕写成这一轮的用户消息。没人接的旧话题不写进去。"""
        name = item.get("uname") or "观众"
        kind = item.get("kind") or "danmaku"
        if kind == "gift":
            return self._gift_prompt(item)
        if kind == "enter":
            return self._enter_prompt(item)
        # 主播本人弹幕：完全服从指示，不做普通粉丝复述套路。
        ctx = self.context_block()
        if is_nonoko_name(name):
            return (
                owner_obey_block()
                + (ctx + "\n" if ctx else "")
                + "这是主播本人「%s」在弹幕里下的指示：%s\n"
                "按指示口播 1～3 句；不要当成路人弹幕去抬杠或弱智吧拆词盖过指令。\n"
                "%s"
                % (name, item["msg"], self._fresh_line_rule("主播指示"))
            )
        topic = self._topic or {}
        # 口头装送礼：走识破话术，不当真礼物感谢。
        if is_fake_gift_claim(item.get("msg")):
            return self._fake_gift_prompt(item, ctx)
        # 弹幕口播固定节奏：先复述 →（前端会停顿装作思考）→ 再正式回复。
        shape = (
            "口播结构必须分句（lines），严格按顺序：\n"
            "1）第一句：短复述或概括这条弹幕（可带观众名，如「哦，某某说……」），只复述大意，不要在第一句里给答案。\n"
            "2）第二句起：可先带一点思考语气（「嗯…」「让我想想」「这个啊」），再给出真正回复。\n"
            "总共 2～3 句。有梗就用梗接，别解释梗从哪来。优先回本条新弹幕；可结合上下文关联，但没写进上下文的当没看见。"
        )
        level = int(item.get("fans_level") or 0)
        medal = _fans_hint(level)
        fresh = self._fresh_line_rule("弹幕回复")
        head = (ctx + "\n") if ctx else ""
        persons = pronoun_block(name)
        if item.get("follow") and topic.get("text"):
            who = topic.get("uname") or "观众"
            return (
                head
                + persons
                + "这是直播间里的一条弹幕，要说给观众听。\n"
                "观众「%s」说：%s\n"
                "fans_medal_level=%d。等级越高说明打钱越多，越要重视。\n"
                "%s\n"
                "这句还在接「%s」发起的话题：%s\n"
                "结合上下文顺着这个话题接。\n"
                "%s\n"
                "%s"
                % (name, item["msg"], level, medal, who, topic.get("text"), shape, fresh)
            )
        return (
            head
            + persons
            + "这是直播间里的一条弹幕，要说给观众听。\n"
            "观众「%s」说：%s\n"
            "fans_medal_level=%d。等级越高说明打钱越多，越要重视。\n"
            "%s\n"
            "这是本轮要回的新弹幕；结合上面上下文理解，不要无视语境，也不要复读旧回复。\n"
            "%s\n"
            "%s"
            % (name, item["msg"], level, medal, shape, fresh)
        )

    def _fake_gift_prompt(self, item, ctx=""):
        """口头装送礼：明确不是开放平台礼物事件，要聪明识破、可爱反击。"""
        name = item.get("uname") or "观众"
        level = int(item.get("fans_level") or 0)
        styles = (
            "腹黑笑着拆穿",
            "弱智吧式字面理解「嘴上送」",
            "撒娇反击但点明没检测到礼物",
            "二次元/系统提示没收到礼物包",
            "自夸很聪明分得清真假礼物",
        )
        style = random.choice(styles)
        return (
            ((ctx + "\n") if ctx else "")
            + pronoun_block(name)
            + "这是普通弹幕，不是 LIVE_OPEN_PLATFORM_SEND_GIFT 礼物事件。\n"
            "观众「%s」在文字里装作送了礼物/上舰/打赏，内容是：%s\n"
            "fans_medal_level=%d。\n"
            "%s\n"
            "系统没有收到真实礼物推送。你很聪明，分得清真假：\n"
            "- 不要当成真送礼去隆重感谢，不要念礼物价格。\n"
            "- 口播 2～3 句：先短复述对方在说送礼；再明确拆穿「文字不算数 / 我分辨得出来」；"
            "可用腹黑或可爱语气，可半开玩笑邀他真送舰长或盲盒，但别骂人、别冷暴力。\n"
            "本轮切入角度：%s。\n"
            "%s"
            "expression 可用 1心眼、腹黑、1星。\n"
            % (
                name,
                item.get("msg") or "",
                level,
                _fans_hint(level),
                style,
                self._fresh_line_rule("识破假礼物"),
            )
        )

    def note_said(self, say):
        """记下刚说过的口播，供后续轮次避开复用。"""
        clean = _clean(say, 160)
        if not clean:
            return
        with self._lock:
            self._recent_says.append(clean)
            if len(self._recent_says) > 24:
                del self._recent_says[:12]

    # 兼容旧调用名。
    def note_welcome(self, say):
        self.note_said(say)

    def _avoid_says_block(self, label="口播"):
        """列出本场近期台词，禁止复用。"""
        with self._lock:
            recent = list(self._recent_says[-10:])
        if not recent:
            return ""
        return (
            "下面是本场已经说过的%s，禁止复用原文、同义改写或同一套句式：\n- "
            % label
            + "\n- ".join(recent)
            + "\n"
        )

    def _fresh_line_rule(self, kind_label):
        """所有口播统一：每次全新，不调用会话旧话。"""
        return (
            "%s必须每次全新创作：不要调用、不要沿用、不要改写会话记忆里之前说过的任何话。\n"
            "%s"
            % (kind_label, self._avoid_says_block(kind_label))
        )

    def _gift_prompt(self, item):
        """礼物弹幕：按价格和粉丝牌等级，非常开心地道谢。"""
        name = item.get("uname") or "观众"
        gift = item.get("gift_name") or "礼物"
        num = int(item.get("gift_num") or 1)
        price = int(item.get("price") or 0)
        level = int(item.get("fans_level") or 0)
        # 开放平台 price 单位是金瓜子，1000 金瓜子约 1 元。
        yuan = price / 1000.0
        if "总督" in gift or level >= 21 or yuan >= 200:
            heat = "这是很重的一份心意，要隆重、大声、非常开心地感谢，让全场都高兴。"
            face = "expression 用 1总督 或 谢谢舰长大人爱你们 或 1心眼。"
        elif "提督" in gift or level >= 16 or yuan >= 50:
            heat = "粉丝牌不低、礼物也不轻，感谢要更热烈，带动别人一起开心。"
            face = "expression 用 1提督 或 1舰长 或 1心眼。"
        elif "舰长" in gift or level >= 10 or yuan >= 10:
            heat = "要明显比普通弹幕更开心，认真谢谢，并祝对方玩得开心。"
            face = "expression 用 1舰长 或 谢谢舰长大人爱你们 或 1星。"
        else:
            heat = "哪怕礼物不大，也要真心、开心地谢谢，让对方高兴。"
            face = "expression 用 1心眼 或 1星 或 谢谢舰长大人爱你们。"
        count = "%d 个" % num if num > 1 else "1 个"
        styles = (
            "夸张开心尖叫式感谢",
            "腹黑半句再认真道谢",
            "弱智吧式字面理解礼物名",
            "二次元/游戏掉宝比喻",
            "撒娇求继续但先谢眼前这份",
            "带动全场一起起哄感谢",
            "彗星/甜麦人设小梗接感谢",
        )
        style = random.choice(styles)
        ctx = self.context_block()
        return (
            (ctx + "\n" if ctx else "")
            + pronoun_block(name)
            + "这是直播间礼物，不是普通聊天，必须优先、开心地感谢，不要冷淡，不要思考停顿。\n"
            "观众「%s」送出了 %s「%s」。礼物单价约 %.2f 元（金瓜子 %d）。\n"
            "fans_medal_level=%d（粉丝牌等级；等级越高说明打钱越多，越要重视）。\n"
            "%s\n"
            "%s\n"
            "口播 1～2 句：点名观众和礼物名，表达非常开心的感谢，并让对方和直播间其他人一起高兴。\n"
            "本轮切入角度：%s。\n"
            "%s"
            "不要复述系统说明，不要讲价格计算公式。%s"
            % (
                name, count, gift, yuan, price, level,
                _fans_hint(level), heat, style,
                self._fresh_line_rule("感谢礼物"),
                face,
            )
        )

    def _enter_prompt(self, item):
        """进房事件：第一次欢迎光临；隔了 30 分钟再来就欢迎回家。"""
        name = item.get("uname") or "观众"
        level = int(item.get("fans_level") or 0)
        if item.get("home"):
            line = "这是「%s」离开超过 30 分钟后又进房。请用 1～2 句开心的短台词欢迎他回家，一定要点名。" % name
        else:
            line = "这是「%s」这次开播后第一次进房。请用 1～2 句短台词欢迎并感谢光临，一定要点名。" % name
        # 强制换说法，避免持久会话把上一轮欢迎又念一遍。
        styles = (
            "腹黑半句调侃进房",
            "弱智吧式字面理解「进来」",
            "撒娇短句欢迎",
            "二次元/游戏比喻接人",
            "自嘲直播不容易再欢迎",
            "像见到老粉一样热情",
            "用星星/彗星人设玩一小梗",
        )
        style = random.choice(styles)
        ctx = self.context_block()
        return (
            (ctx + "\n" if ctx else "")
            + pronoun_block(name)
            + "这是观众进入直播间，不是聊天弹幕。必须开口，不能忽略。\n"
            + line + "\n"
            "fans_medal_level=%d。等级越高说明打钱越多，越要重视；高等级可更热情点名。\n"
            "%s\n"
            "本轮切入角度：%s。\n"
            "%s"
            "不要假装对方说了一句话，不要思考停顿，不要复述系统说明。\n"
            "expression 可用 挥手、1星、1心眼，高等级可用 1舰长。\n"
            % (level, _fans_hint(level), style, self._fresh_line_rule("进房欢迎"))
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
                    "uface": item.get("uface") or "",
                    "kind": item.get("kind") or "danmaku",
                }
                for item in self._board[-24:]
            ]
            connected = self.connected
        if not cfg["idcode"]:
            hint = "还没有身份码"
        elif not connected:
            hint = "正在连接弹幕"
        else:
            hint = "弹幕已接上"
        return {
            "configured": bool(cfg["idcode"]),
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

    def _room_quiet_locked(self):
        """须持锁。1 分钟内无非进房事件，且板上没有待回/正在说的聊天或礼物。"""
        now = time.time()
        if now - float(self._last_non_enter_ts or 0) < _ENTER_QUIET:
            return False
        for item in self._board:
            if item["state"] not in ("new", "queued", "speaking"):
                continue
            if (item.get("kind") or "danmaku") != "enter":
                return False
        return True

    def _drop_busy_enters(self):
        """热闹时把板上还没回的进房欢迎直接跳过。"""
        if self._room_quiet_locked():
            return
        for item in self._board:
            if item.get("kind") == "enter" and item["state"] in ("new", "queued"):
                item["state"] = "skipped"

    def _fill_reply_slot(self):
        """把续聊分最高的一条标成 queued；其余保持 new，按分排队稍后一一回。"""
        self._drop_busy_enters()
        pool = [item for item in self._board if item["state"] in ("new", "queued")]
        if not pool:
            return
        winner = self._pick(pool)
        for item in pool:
            if item is winner:
                item["state"] = "queued"
            elif item["state"] == "queued":
                # 被更高分顶掉时退回序列，不丢掉。
                item["state"] = "new"

    def _chat_score(self, item):
        """续聊打分：问句/点名/接话题优先，灌水靠后。越高越先回。"""
        raw = str(item.get("msg") or "")
        folded = re.sub(r"\s+", "", raw)
        score = 0
        # 问句最容易接下去聊。
        if "？" in raw or "?" in raw:
            score += 10
        if any(word in raw for word in ("吗", "呢", "怎么", "为什么", "为啥", "啥", "哪", "几", "谁")):
            score += 6
        # 点名主播或第二人称，互动意图强。
        if any(word in raw for word in ("主播", "你", "姐姐", "宝宝", "琴", "nonoko", "Nonoko", "诺诺")):
            score += 5
        # 梗能续玩。
        if has_meme(raw):
            score += 5
        # 长度适中更有内容；极短灌水压分。
        n = len(folded)
        if 6 <= n <= 60:
            score += 4
        elif 3 <= n <= 5:
            score += 1
        elif n <= 2:
            score -= 6
        elif n > 90:
            score -= 2
        low = folded.lower()
        if folded in _FILLER_MSGS or low in _FILLER_MSGS:
            score -= 8
        if re.fullmatch(r"[哈呵嘿6哇哦啊噢嗯]+", folded):
            score -= 8
        if re.fullmatch(r"[好不错棒]+", folded):
            score -= 5
        # 粉丝牌略加分，但不盖过「能不能聊下去」。
        score += min(int(item.get("fans_level") or 0), 12)
        # 主播本人指示绝对优先于路人闲聊。
        if is_nonoko_name(item.get("uname") or ""):
            score += 40
        return score

    def _priority(self, item):
        """礼物最高；普通弹幕看续聊分；进房仅安静时才有低优先级。"""
        kind = item.get("kind") or "danmaku"
        if kind == "gift":
            price = int(item.get("price") or 0)
            yuan = price / 1000.0
            level = int(item.get("fans_level") or 0)
            return 80 + min(int(yuan), 80) + min(level, 40)
        if kind == "enter":
            if not self._room_quiet_locked():
                return -100
            level = int(item.get("fans_level") or 0)
            return 1 + min(level, 20)
        return self._chat_score(item)

    def _pick(self, pool):
        """按续聊分挑下一条；同分偏早到的，保证其余按序列陆续回。"""
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
            # 接上当前话题的普通弹幕加分，更容易把对话续下去。
            topic_bonus = 8 if overlap and blob and (item.get("kind") or "danmaku") == "danmaku" else 0
            if overlap >= 2:
                topic_bonus += 4
            fun = self._priority(item) + topic_bonus
            item["score"] = fun
            ranked.append((item, fun, overlap, has_meme(item["msg"])))
        # 分高优先；同分按到达时间，形成稳定回复序列。
        ranked.sort(key=lambda row: (-row[1], row[0]["ts"], row[0]["id"]))
        chosen = ranked[0][0]
        overlap = ranked[0][2]
        kind = chosen.get("kind") or "danmaku"
        chosen["follow"] = kind == "danmaku" and bool(overlap) and origin in ("danmaku", "ask", "self")
        if origin in ("danmaku", "ask") and not overlap:
            # 这条没接上一个话题，下一句不要再硬续。
            self._topic = None
        return chosen

    def _trim_board(self):
        """板子超限时先删已完成/跳过；必要时删续聊分最低的旧 new。"""
        while len(self._board) > _BOARD_KEEP:
            drop_at = None
            for state in ("done", "skipped"):
                for index, item in enumerate(self._board):
                    if item["state"] == state:
                        drop_at = index
                        break
                if drop_at is not None:
                    break
            if drop_at is None:
                # 正在回/排队的不删；在 new 里丢掉分最低且最早的。
                candidates = [
                    (index, item)
                    for index, item in enumerate(self._board)
                    if item["state"] == "new"
                ]
                if not candidates:
                    return
                candidates.sort(
                    key=lambda row: (
                        int(row[1].get("score") if row[1].get("score") is not None else self._priority(row[1])),
                        row[1]["ts"],
                        row[1]["id"],
                    )
                )
                drop_at = candidates[0][0]
            self._board.pop(drop_at)

    def _loop(self):
        told = False
        while True:
            cfg = load_bili()
            if not cfg["idcode"]:
                self.connected = False
                time.sleep(5)
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
                    print("弹幕还没连上:", _quiet_error(exc, cfg.get("idcode") or ""), flush=True)
                    told = True
                time.sleep(3)
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
        if not isinstance(msg, dict):
            return
        cmd, data = _event_parts(msg)
        if cmd in _GIFT_CMDS:
            self._offer_gift(data)
            return
        if cmd in _ENTER_CMDS:
            self._offer_enter(data)
            return
        if cmd not in _DANMU_CMDS and _norm_cmd(msg.get("type")) != "DANMU":
            if cmd and cmd not in ("DANMU", "MESSAGE", "PING", "HEARTBEAT"):
                print("未识别弹幕事件", cmd, flush=True)
            return
        if cmd and cmd not in _DANMU_CMDS:
            return
        self._offer(
            data.get("msg_id"),
            _person_name(data) or data.get("uname"),
            data.get("msg"),
            data.get("timestamp"),
            fans_level=_fans_level(data),
            uface=_uface(data),
        )

    def _offer_gift(self, data):
        """礼物：记下名称、价格和粉丝牌等级，排队时优先感谢。"""
        gift = _clean(data.get("gift_name"), 40) or "礼物"
        num = _as_int(data.get("gift_num") or data.get("num") or 1, 1)
        if num < 1:
            num = 1
        price = _as_int(data.get("price"), 0)
        level = _fans_level(data)
        count = "%d个" % num if num > 1 else ""
        text = "送出%s%s" % (count, gift)
        self._offer(
            data.get("msg_id"),
            _person_name(data) or data.get("uname"),
            text,
            data.get("timestamp"),
            kind="gift",
            gift_name=gift,
            gift_num=num,
            price=price,
            fans_level=level,
            uface=_uface(data),
        )

    def _offer_enter(self, data):
        """进房：房间安静才欢迎；30 分钟内同 uname 不重复；超过则欢迎回家。"""
        name = _person_name(data)
        if name and is_redline(name):
            return
        if not name:
            name = "朋友"
        now = time.time()
        home = False
        level = _fans_level(data)
        face = _uface(data)
        with self._lock:
            # 弹幕热闹时不欢迎，也不记进房时间，安静后再进仍可欢迎。
            if not self._room_quiet_locked():
                print("进房跳过（弹幕热闹）：%s" % name, flush=True)
                return
            for item in self._board:
                if item.get("kind") == "enter" and item.get("uname") == name and item["state"] in ("new", "queued", "speaking"):
                    return
            # 没有真实昵称时不记档，避免所有匿名挤成同一个人。
            if name != "朋友":
                last = self._visits.get(name)
                if last and now - last < _ENTER_GAP:
                    print("进房跳过（30分钟内）：%s" % name, flush=True)
                    return
                home = bool(last)
                self._visits[name] = now
                _save_visits(self._visits)
        text = "又回到直播间" if home else "进入了直播间"
        print("进房：%s%s" % (name, "（欢迎回家）" if home else ""), flush=True)
        self._offer(
            data.get("msg_id"),
            name,
            text,
            data.get("timestamp"),
            kind="enter",
            enter_home=home,
            fans_level=level,
            uface=face,
        )

    def _offer(self, msg_id, uname, msg, stamp=None, kind="danmaku", gift_name="", gift_num=0, price=0, fans_level=0, enter_home=False, uface=""):
        """收一条弹幕。红线直接丢，不进队列。"""
        text = _clean(msg, 180)
        if not text:
            return
        if is_redline(text):
            return
        name = _clean(uname, 24)
        if not name or is_redline(name):
            name = "观众"
        key = "%s\n%s\n%s\n%s\n%s" % (kind, msg_id or "", name, text, stamp or "")
        with self._lock:
            if key in self._seen:
                return
            self._seen.append(key)
            if len(self._seen) > 400:
                del self._seen[:200]
            # 非进房事件刷新热闹时钟，并顺手清掉还没回的进房欢迎。
            if kind != "enter":
                self._last_non_enter_ts = time.time()
                for item in self._board:
                    if item.get("kind") == "enter" and item["state"] in ("new", "queued"):
                        item["state"] = "skipped"
            item = {
                "id": self._next_id,
                "seq": key,
                "msg_id": str(msg_id or ""),
                "uname": name,
                "msg": text,
                "kind": kind,
                "ts": time.time(),
                "fails": 0,
                "state": "new",
                "follow": False,
                # 粉丝牌等级：越高说明打钱越多。
                "fans_level": int(fans_level or 0),
                # 开放平台头像 URL。
                "uface": str(uface or "").strip()[:500],
                "score": 0,
            }
            if kind == "gift":
                item["gift_name"] = gift_name or "礼物"
                item["gift_num"] = gift_num or 1
                item["price"] = price
            if kind == "enter" and enter_home:
                item["home"] = True
            # 同内容已回过：上板展示，但不进入待回。礼物用消息号区分，连送仍会谢。
            if self._content_key(item) in self._replied:
                item["state"] = "skipped"
            else:
                item["score"] = self._priority(item)
            self._board.append(item)
            self._next_id += 1
            self._trim_board()
            if item["state"] == "new":
                self._fill_reply_slot()


feed = DanmakuFeed()
