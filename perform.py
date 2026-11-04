# -*- coding: utf-8 -*-
"""把一句口播编成表情、动作和口型时间轴。

表情是绝对参数，动作是加在当前值上的曲线。
模型里没有的参数由播放端跳过。
"""

from pathlib import Path
import re

MOODS = {
    "neutral": {},
    "happy": {
        "ParamEyeLSmile": 1, "ParamEyeRSmile": 1, "ParamMouthForm": 0.85,
        "ParamBrowLY": 0.35, "ParamBrowRY": 0.35,
    },
    "shy": {
        "Param2": 1, "ParamCheek": 0.85,
        "ParamEyeLSmile": 0.55, "ParamEyeRSmile": 0.55,
        "ParamMouthForm": 0.35, "ParamAngleZ": 8, "ParamEyeBallY": -0.45,
    },
    "angry": {
        "ParamBrowLY": -1, "ParamBrowRY": -1,
        "ParamBrowLAngle": -1, "ParamBrowRAngle": -1,
        "ParamMouthForm": -0.85, "ParamEyeLOpen": 0.72, "ParamEyeROpen": 0.72,
    },
    "sad": {
        "ParamMouthForm": -0.55, "ParamBrowLForm": -0.8, "ParamBrowRForm": -0.8,
        "ParamBrowLY": -0.35, "ParamBrowRY": -0.35,
        "ParamAngleY": -8, "ParamEyeBallY": -0.5, "ParamEyeLOpen": 0.65, "ParamEyeROpen": 0.65,
    },
    "surprise": {
        "ParamEyeLOpen": 1, "ParamEyeROpen": 1,
        "ParamBrowLY": 1, "ParamBrowRY": 1, "ParamMouthForm": 0.15,
    },
    "star": {
        "Param": 1, "ParamEyeLSmile": 1, "ParamEyeRSmile": 1, "ParamMouthForm": 0.55,
    },
    "think": {
        "ParamEyeBallX": -0.5, "ParamEyeBallY": 0.35,
        "ParamBrowLY": 0.45, "ParamBrowLAngle": 0.35,
        "ParamMouthForm": -0.1, "ParamAngleX": -6,
    },
}

# 跨对话保持的整体动态。tracks 是一圈循环，数值是加在当前姿态上的偏移。
# 幅度收着，areazil 的身体角只有大约 ±10。
VIBES = {
    "still": {
        "label": "安静",
        "hint": "几乎不晃，只留呼吸",
        "period": 1.0,
        "tracks": {},
    },
    "relax": {
        "label": "放松地晃",
        "hint": "慢慢左右换重心，头跟着轻轻歪",
        "period": 5.6,
        "tracks": {
            "ParamAngleZ": [[0, 0], [0.25, 5], [0.5, 0], [0.75, -5], [1, 0]],
            "ParamBodyAngleX": [[0, 0], [0.25, 3.2], [0.5, 0], [0.75, -3.2], [1, 0]],
            "ParamBodyAngleZ": [[0, 0], [0.3, 1.6], [0.7, -1.6], [1, 0]],
            "ParamAngleY": [[0, 1.2], [0.5, -1.4], [1, 1.2]],
        },
    },
    "lively": {
        "label": "轻快地颠",
        "hint": "节奏短一点的小幅度上下和歪头",
        "period": 2.3,
        "tracks": {
            "ParamBodyAngleY": [[0, 0], [0.22, 3.2], [0.48, 0], [0.72, 1.8], [1, 0]],
            "ParamAngleY": [[0, 0], [0.22, 3.6], [0.5, -1], [0.75, 2.2], [1, 0]],
            "ParamAngleZ": [[0, -2], [0.5, 2.6], [1, -2]],
        },
    },
    "shy": {
        "label": "偏着不好意思",
        "hint": "头偏到一边，慢慢小幅挪，不看正前方",
        "period": 6.4,
        "tracks": {
            "ParamAngleZ": [[0, 6], [0.5, 9], [1, 6]],
            "ParamAngleX": [[0, -2], [0.5, 1], [1, -2]],
            "ParamAngleY": [[0, -2.5], [0.5, -4.5], [1, -2.5]],
        },
    },
    "fidget": {
        "label": "坐不住",
        "hint": "小幅度左右看，重心跟着挪一下",
        "period": 3.4,
        "tracks": {
            "ParamAngleX": [[0, 0], [0.22, 6], [0.48, -5], [0.74, 3.5], [1, 0]],
            "ParamAngleZ": [[0, 0], [0.35, -3.5], [0.7, 2.5], [1, 0]],
            "ParamBodyAngleX": [[0, 0], [0.4, 2.2], [0.75, -1.4], [1, 0]],
        },
    },
    "sleepy": {
        "label": "困倦地垂着",
        "hint": "头慢慢低下去，身体跟着沉一点",
        "period": 7.2,
        "tracks": {
            "ParamAngleY": [[0, -3], [0.55, -7], [1, -3]],
            "ParamBodyAngleY": [[0, -1.5], [0.55, -3.2], [1, -1.5]],
            "ParamAngleZ": [[0, 2], [0.5, 4.5], [1, 2]],
        },
    },
    "listen": {
        "label": "侧耳听",
        "hint": "头侧过去，身体微微前倾，像在等下一句",
        "period": 4.8,
        "tracks": {
            "ParamAngleZ": [[0, 5], [0.45, 8], [0.75, 5.5], [1, 5]],
            "ParamAngleX": [[0, 2], [0.5, 4], [1, 2]],
            "ParamBodyAngleX": [[0, 1.5], [0.5, 2.6], [1, 1.5]],
            "ParamAngleY": [[0, 1.5], [0.5, 2.8], [1, 1.5]],
        },
    },
}

_VIBE_ALIAS = {
    "still": "still", "安静": "still", "静止": "still", "calm": "still",
    "relax": "relax", "放松": "relax", "放松地晃": "relax", "sway": "relax", "晃": "relax",
    "lively": "lively", "轻快": "lively", "轻快地颠": "lively", "bounce": "lively",
    "shy": "shy", "害羞": "shy", "不好意思": "shy",
    "fidget": "fidget", "坐不住": "fidget", "不安": "fidget",
    "sleepy": "sleepy", "困": "sleepy", "困倦": "sleepy", "困倦地垂着": "sleepy",
    "listen": "listen", "听": "listen", "侧耳": "listen", "侧耳听": "listen",
}

# 曲线用 0~1 的进度。数值是增量，播放端会加到当前参数上。
ACTS = {
    "nod": {"duration": 0.72, "tracks": {
        "ParamAngleY": [[0, 0], [0.28, -12], [0.55, 5], [0.78, -3], [1, 0]],
    }},
    "shake": {"duration": 0.78, "tracks": {
        "ParamAngleX": [[0, 0], [0.2, 12], [0.45, -12], [0.7, 7], [1, 0]],
    }},
    "tilt": {"duration": 0.9, "tracks": {
        "ParamAngleZ": [[0, 0], [0.35, 14], [0.7, 14], [1, 0]],
    }},
    "look_left": {"duration": 1.05, "tracks": {
        "ParamEyeBallX": [[0, 0], [0.25, -0.75], [0.75, -0.75], [1, 0]],
        "ParamAngleX": [[0, 0], [0.25, -10], [0.75, -10], [1, 0]],
    }},
    "look_right": {"duration": 1.05, "tracks": {
        "ParamEyeBallX": [[0, 0], [0.25, 0.75], [0.75, 0.75], [1, 0]],
        "ParamAngleX": [[0, 0], [0.25, 10], [0.75, 10], [1, 0]],
    }},
    "look_up": {"duration": 0.95, "tracks": {
        "ParamEyeBallY": [[0, 0], [0.3, 0.65], [0.72, 0.65], [1, 0]],
        "ParamAngleY": [[0, 0], [0.3, 8], [0.72, 8], [1, 0]],
    }},
    "look_down": {"duration": 0.95, "tracks": {
        "ParamEyeBallY": [[0, 0], [0.3, -0.6], [0.72, -0.6], [1, 0]],
        "ParamAngleY": [[0, 0], [0.3, -6], [0.72, -6], [1, 0]],
    }},
    "wink": {"duration": 0.38, "tracks": {
        "ParamEyeLOpen": [[0, 0], [0.35, -1], [0.62, -1], [1, 0]],
    }},
    "bounce": {"duration": 0.55, "tracks": {
        "ParamAngleY": [[0, 0], [0.35, 8], [0.7, -2], [1, 0]],
        "ParamBodyAngleY": [[0, 0], [0.35, 5], [1, 0]],
    }},
    "lean": {"duration": 0.85, "tracks": {
        "ParamAngleZ": [[0, 0], [0.4, 12], [0.75, 12], [1, 0]],
        "ParamBodyAngleX": [[0, 0], [0.4, 6], [0.75, 6], [1, 0]],
        "ParamBodyAngleZ": [[0, 0], [0.4, 5], [0.75, 5], [1, 0]],
    }},
}

VOICES = {
    "zh": "zh-CN-XiaoyiNeural",
    "ja": "ja-JP-NanamiNeural",
    "en": "en-US-AriaNeural",
}

# 页面上能选的嗓音。pitch 是音高（Hz），rate 是语速（百分数），都是相对这套嗓音的默认特色。
VOICE_PRESETS = (
    {"id": "xiaoyi", "lang": "zh", "label": "晓伊", "voice": "zh-CN-XiaoyiNeural", "pitch": -7, "rate": -5},
    {"id": "xiaomeng", "lang": "zh", "label": "晓梦 · 软一点", "voice": "zh-CN-XiaomengNeural", "pitch": 6, "rate": 0},
    {"id": "xiaoxiao", "lang": "zh", "label": "晓晓 · 普通", "voice": "zh-CN-XiaoxiaoNeural", "pitch": 0, "rate": 0},
    {"id": "xiaohan", "lang": "zh", "label": "晓涵 · 慢一点", "voice": "zh-CN-XiaohanNeural", "pitch": -2, "rate": -8},
    {"id": "xiaomo", "lang": "zh", "label": "晓墨 · 沉一点", "voice": "zh-CN-XiaomoNeural", "pitch": -4, "rate": -4},
    {"id": "nanami", "lang": "ja", "label": "七海 · 日语", "voice": "ja-JP-NanamiNeural", "pitch": 4, "rate": 0},
    {"id": "aoi", "lang": "ja", "label": "碧衣 · 日语", "voice": "ja-JP-AoiNeural", "pitch": 8, "rate": 6},
    {"id": "aria", "lang": "en", "label": "Aria · 英语", "voice": "en-US-AriaNeural", "pitch": 0, "rate": 0},
    {"id": "jenny", "lang": "en", "label": "Jenny · 英语", "voice": "en-US-JennyNeural", "pitch": 4, "rate": 4},
)

_TAG = re.compile(r"<\s*([A-Za-z_]+)(?:\s*:\s*([0-9]*\.?[0-9]+))?\s*>")
_MOOD_WORDS = (
    ("angry", ("生气", "可恶", "哼", "angry", "hate")),
    ("sad", ("难过", "伤心", "对不起", "呜", "sad", "sorry")),
    ("shy", ("害羞", "不好意思", "羞")),
    ("surprise", ("哇", "诶", "咦", "天哪", "wow")),
    ("star", ("星星", "好喜欢")),
    ("think", ("让我想想", "想想", "思考", "hmm")),
    ("happy", ("哈哈", "开心", "太好了", "笑死", "happy")),
)


_VIBE_PATH = Path(__file__).resolve().parent / ".vibe"


def load_vibe():
    """读上一轮留下的整体动态。没有记录时从放松地晃开始。"""
    try:
        text = _VIBE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return normalize_vibe(text or "relax")


def save_vibe(name):
    """记住这一轮结束时的 vibe，下一轮提示词会带上它。"""
    vibe_id = normalize_vibe(name)
    _VIBE_PATH.write_text(vibe_id, encoding="utf-8")
    return vibe_id


def normalize_vibe(name, fallback="relax"):
    """把模型写的中文或英文收成已知的整体动态。不认识就保持原来的。"""
    key = str(name or "").strip().lower()
    if key in VIBES:
        return key
    aliased = _VIBE_ALIAS.get(str(name or "").strip()) or _VIBE_ALIAS.get(key)
    if aliased in VIBES:
        return aliased
    return fallback if fallback in VIBES else "relax"


def public_vibe(name):
    """给页面播放用的一份循环。没有密钥，也没有模型路径。"""
    vibe_id = normalize_vibe(name)
    spec = VIBES[vibe_id]
    return {
        "id": vibe_id,
        "label": spec["label"],
        "hint": spec["hint"],
        "period": spec["period"],
        "tracks": spec["tracks"],
    }


def describe_vibe(name):
    """写进下一次提示词，让模型知道现在的身体状态，并可以换。"""
    spec = VIBES[normalize_vibe(name)]
    choices = "、".join("%s（%s）" % (item_id, item["label"]) for item_id, item in VIBES.items())
    return "当前整体动态 vibe 是 %s（%s：%s）。可以保持，也可以换成 %s。" % (
        normalize_vibe(name), spec["label"], spec["hint"], choices,
    )


def _cue_vibe(cues):
    """整体动态可以写在 cues 里，字段名是 vibe，不是 act。"""
    found = None
    acts = []
    for item in cues or []:
        if not isinstance(item, dict):
            continue
        if item.get("vibe") and not item.get("act"):
            found = item.get("vibe")
            continue
        acts.append(item)
    return found, acts


def is_hand_clip(name):
    """与前端一致：模型自带里以 3 开头的都是手部/道具。"""
    text = str(name or "").strip()
    return bool(text) and text[0] == "3"


# 按台词内容匹配表情（闲聊兜底用）。不含发型换装/拉琴话筒，那些要台词点名才行。
_CLIP_BY_TEXT = (
    (r"哭|难过|伤心|可怜|呜呜|泪", ("1哭耳", "1泪1", "1泪2", "2哭嘴")),
    (r"吓|惊吓|吓一跳|突然|哇+", ("1惊", "1星")),
    (r"惊喜|居然|太棒|厉害", ("1星", "1惊")),
    (r"气|哼+|讨厌|烦死|生气", ("1气",)),
    (r"晕|搞不懂|什么情况|莫名其妙", ("1晕",)),
    (r"无语|呵呵|汗|离谱", ("1汗", "1黑")),
    (r"怕|害怕|紧张|慌", ("1鼻线",)),
    (r"喜欢|爱了|心动|爱你", ("1心眼", "3心")),
    (r"害羞|不好意思|脸红|羞", ("1红1", "1红2", "3扣手指")),
    (r"可爱|嘿嘿|卖萌", ("1红1", "1红2", "1星")),
    (r"再见|拜拜|回见|挥手", ("3挥手", "挥手")),
    (r"手机|刷一下|看看手机", ("3手机",)),
    (r"游戏|开一把|手柄", ("3手柄",)),
    (r"谢谢|感谢|礼物|舰长|提督|总督|送出", ("谢谢舰长大人爱你们", "1舰长", "1心眼", "1星")),
    (r"欢迎|进来啦|进直播间|光临", ("挥手", "1星", "1心眼")),
    (r"看看|凑近|前倾|盯", ("2前倾",)),
    (r"小提琴|拉琴|弹琴", ("3小提琴",)),
    (r"唱歌|唱一首|话筒|麦克风", ("3话筒",)),
)

_CLIP_BY_MOOD = {
    "happy": ("1星", "1心眼"),
    "shy": ("1红1", "1红2", "3扣手指"),
    "angry": ("1气",),
    "sad": ("1哭耳", "1泪1", "2哭嘴"),
    "surprise": ("1惊", "1星"),
    "star": ("1星", "1心眼"),
    "think": ("1晕", "1汗"),
    "cool": ("1黑", "1汗"),
}


def suggest_expression_for_text(text, mood=None):
    """根据台词（和 mood）挑一个贴合的模型表情 id；对不上就按心情，再不行给轻表情。"""
    blob = str(text or "")
    for pattern, clips in _CLIP_BY_TEXT:
        if re.search(pattern, blob):
            return clips[0]
    mood_key = str(mood or "").strip().lower()
    mood_clips = _CLIP_BY_MOOD.get(mood_key)
    if mood_clips:
        return mood_clips[0]
    # 闲聊默认：轻轻害羞/星星，避免假动作乱跳。
    return "1红1"


def fill_missing_expressions(lines):
    """句子没写 expression 时，按台词补一个贴合的，避免前端再随机乱挂。"""
    for line in lines:
        if line.get("expression"):
            continue
        face = suggest_expression_for_text(line.get("say"), line.get("mood"))
        if face and face not in BANNED_EXPRESSIONS:
            line["expression"] = face
    return lines


# 禁用的模型表情：口播与自动匹配都不要用。
BANNED_EXPRESSIONS = frozenset({"1舌"})


def strip_banned_expressions(lines):
    """丢掉禁用表情；若本句因此空了，再按台词补一个允许的。"""
    for line in lines:
        face = str(line.get("expression") or "").strip()
        if face in BANNED_EXPRESSIONS:
            line.pop("expression", None)
            alt = suggest_expression_for_text(line.get("say"), line.get("mood"))
            if alt and alt not in BANNED_EXPRESSIONS:
                line["expression"] = alt
    return lines


def thin_stage_actions(lines):
    """压动作频率：每句最多 1 个短动作；整轮最多保留一个手部 clip。"""
    hand_kept = False
    for line in lines:
        cues = [item for item in (line.get("cues") or []) if isinstance(item, dict) and item.get("act")]
        line["cues"] = cues[:1]
        motion = line.get("motion")
        expression = line.get("expression")
        hand_expr = is_hand_clip(expression)
        hand_motion = is_hand_clip(motion)
        if hand_expr or hand_motion:
            if hand_kept:
                if hand_expr:
                    line.pop("expression", None)
                if hand_motion:
                    line.pop("motion", None)
            else:
                hand_kept = True
                # 同时有手部表情和手部动作时只留表情，避免双手叠上。
                if hand_expr and hand_motion:
                    line.pop("motion", None)
    return lines


def _line_dict(say, mood, cues, vibe=None, motion=None, expression=None):
    text = str(say or "").strip()
    if not text:
        return None
    line = {"say": text[:400], "mood": mood, "cues": cues if isinstance(cues, list) else []}
    if vibe:
        line["vibe"] = vibe
    # 模型文件里的动作和表情。播放端只认名单里的名字。
    clip = str(motion or "").strip()
    face = str(expression or "").strip()
    if clip:
        line["motion"] = clip[:80]
    if face:
        line["expression"] = face[:80]
    return line


def _distribute(texts, mood, cues):
    """一个 say 数组配一份 cues 时，按 at 把短动作分进对应的那一句。"""
    count = len(texts)
    lines = []
    for index, text in enumerate(texts):
        own = []
        for cue in cues:
            if not isinstance(cue, dict):
                continue
            try:
                at = float(cue.get("at", 0))
            except (TypeError, ValueError):
                at = 0
            start = index / count
            end = (index + 1) / count
            if at < start or (at >= end and index != count - 1):
                continue
            remapped = dict(cue)
            remapped["at"] = min(1.0, max(0.0, (at - start) * count))
            own.append(remapped)
        lines.append(_line_dict(text, mood, own))
    return [line for line in lines if line]


def normalize_reply(data, current="relax"):
    """把模型的 JSON 收成多句口播，并决定这一轮结束时的 vibe。

    say 可以是字符串或字符串数组。更完整的写法是 lines，每一句自己的 mood、cues、vibe。
    """
    if not isinstance(data, dict):
        data = {}
    current_id = normalize_vibe(current)
    cue_vibe, shared_cues = _cue_vibe(data.get("cues") if isinstance(data.get("cues"), list) else [])
    lines = []
    raw_lines = data.get("lines")
    if isinstance(raw_lines, list) and raw_lines:
        for item in raw_lines:
            if isinstance(item, str):
                lines.append(_line_dict(item, data.get("mood"), [], None, data.get("motion"), data.get("expression")))
                continue
            if not isinstance(item, dict):
                continue
            item_vibe, item_cues = _cue_vibe(item.get("cues") if isinstance(item.get("cues"), list) else [])
            say = item.get("say") if item.get("say") is not None else item.get("text")
            if isinstance(say, list):
                say = " ".join(str(part) for part in say)
            lines.append(_line_dict(
                say,
                item.get("mood", data.get("mood")),
                item_cues,
                item.get("vibe") or item_vibe,
                item.get("motion", data.get("motion")),
                item.get("expression", data.get("expression")),
            ))
    else:
        say = data.get("say")
        if isinstance(say, list):
            texts = [str(part).strip() for part in say if str(part).strip()]
            lines.extend(_distribute(texts, data.get("mood"), shared_cues))
        else:
            lines.append(_line_dict(say, data.get("mood"), shared_cues, None, data.get("motion"), data.get("expression")))
    # 口播句子不宜太碎；动作另行压频，避免每句都挥手/换道具。
    lines = thin_stage_actions([line for line in lines if line][:4])
    # 没给表情时按台词补，闲聊就不会再随机甩无关动作。
    lines = fill_missing_expressions(lines)
    lines = strip_banned_expressions(lines)
    if not lines:
        raise ValueError("没有要说的话")
    start = normalize_vibe(data.get("vibe") or cue_vibe or current_id, current_id)
    if lines[0].get("vibe"):
        start = normalize_vibe(lines[0]["vibe"], start)
    # 和上一句相同的 vibe 不再往下传，播放端就不会重新做一次过渡。
    running = start
    for line in lines:
        if not line.get("vibe"):
            continue
        chosen = normalize_vibe(line["vibe"], running)
        if chosen == running:
            line.pop("vibe", None)
        else:
            line["vibe"] = chosen
            running = chosen
    return {"vibe": start, "endVibe": running, "lines": lines}


def catalog():
    """给外部模型和页面用的动作清单，不含密钥。"""
    return {
        "moods": sorted(MOODS),
        "vibes": [
            {"id": name, "label": spec["label"], "hint": spec["hint"]}
            for name, spec in VIBES.items()
        ],
        "acts": [
            {"id": name, "duration": spec["duration"], "params": sorted(spec["tracks"])}
            for name, spec in ACTS.items()
        ],
        "voices": VOICES,
        "voicePresets": list(VOICE_PRESETS),
        "tagExample": "<shy><nod>你好呀，<tilt:0.6>今天也想见你。",
        "mouth": "口型由文本音素生成，不由模型填写",
    }


def detect_lang(text):
    """按假名、汉字、拉丁字母判断口播语言。"""
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if kana >= 2 and kana >= han:
        return "ja"
    if latin >= 3 and latin > han * 2:
        return "en"
    return "zh"


def estimate_seconds(text, lang):
    """没有音频时长时，用字数估算，播放端会改成真实时长。"""
    stripped = text.strip()
    if lang == "en":
        words = max(1, len(stripped.split()))
        return max(0.8, words / 2.5)
    rate = 4.6 if lang == "zh" else 5.0
    return max(0.8, max(1, len(stripped)) / rate)


def _guess_mood(text):
    for mood, words in _MOOD_WORDS:
        if any(word in text for word in words):
            return mood
    return "neutral"


def _guess_cues(text, mood):
    if mood == "angry":
        return [{"at": 0.12, "act": "shake"}]
    if mood == "think":
        return [{"at": 0.2, "act": "look_left"}, {"at": 0.55, "act": "tilt"}]
    if mood == "shy":
        cues = [{"at": 0.15, "act": "look_down"}]
    elif re.search(r"[?？]|吗|呢|か", text):
        cues = [{"at": 0.12, "act": "tilt"}, {"at": 0.5, "act": "look_up"}]
    elif re.search(r"[!！]", text):
        cues = [{"at": 0.08, "act": "bounce"}]
    else:
        cues = [{"at": 0.16, "act": "nod"}]
    clauses = [part for part in re.split(r"[。！？!?；;]", text) if part.strip()]
    if len(clauses) >= 3:
        cues.append({"at": 0.62, "act": "nod"})
    return cues[:3]


# 情绪只轻轻带一下嘴角。张嘴和扁圆由 viseme 按文本算。
MOUTH_BIAS = {
    "neutral": 0.0,
    "happy": 0.22,
    "shy": 0.12,
    "angry": -0.32,
    "sad": -0.22,
    "surprise": 0.04,
    "star": 0.18,
    "think": 0.0,
}


def _parse_tags(text):
    mood = None
    cues = []
    pieces = []
    cursor = 0
    spoken_len = 0
    for match in _TAG.finditer(text):
        chunk = text[cursor:match.start()]
        pieces.append(chunk)
        spoken_len += len(chunk.strip())
        name = match.group(1).lower()
        at_text = match.group(2)
        if name in MOODS:
            mood = name
        elif name in ACTS:
            cues.append({"at": float(at_text) if at_text else None, "act": name, "mark": spoken_len})
        cursor = match.end()
    pieces.append(text[cursor:])
    spoken = re.sub(r"\s+", " ", "".join(pieces)).strip()
    total = max(1, len(spoken))
    placed = []
    auto_index = 0
    for cue in cues:
        if cue["at"] is None:
            cue["at"] = min(0.9, cue["mark"] / total)
            if cue["at"] == 0 and auto_index:
                cue["at"] = min(0.9, auto_index / max(1, len(cues)))
        auto_index += 1
        at = min(1.0, max(0.0, float(cue["at"])))
        placed.append({"at": round(at, 3), "act": cue["act"]})
    return spoken, mood, placed


def _explicit_cues(raw):
    placed = []
    if not isinstance(raw, list):
        return placed
    for item in raw:
        if not isinstance(item, dict):
            continue
        act = str(item.get("act") or "").lower()
        if act not in ACTS:
            continue
        try:
            at = float(item.get("at", 0))
        except (TypeError, ValueError):
            at = 0
        placed.append({"at": round(min(1.0, max(0.0, at)), 3), "act": act})
    return placed[:4]


def _clamp_int(value, low, high, fallback):
    """把页面传来的音高、语速收成整数。空值用这套嗓音自己的默认。"""
    if value is None or value == "":
        return fallback
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def resolve_voice(preset_id, lang, pitch=None, rate=None):
    """把音色 id、音高、语速收成 Edge 能用的嗓音名和 prosody。"""
    chosen = None
    if preset_id:
        for item in VOICE_PRESETS:
            if item["id"] == preset_id:
                chosen = item
                break
    if chosen is None:
        for item in VOICE_PRESETS:
            if item["lang"] == lang:
                chosen = item
                break
    if chosen is None:
        chosen = VOICE_PRESETS[0]
    pitch_hz = _clamp_int(pitch, -12, 20, chosen["pitch"])
    rate_pct = _clamp_int(rate, -20, 30, chosen["rate"])
    return {
        "id": chosen["id"],
        "label": chosen["label"],
        "voice": chosen["voice"],
        "pitch": f"{pitch_hz:+d}Hz",
        "rate": f"{rate_pct:+d}%",
    }


def plan(text, mood=None, cues=None, lang=None, voice=None, pitch=None, rate=None):
    """编一段可播放的表演。text 里的 <mood>、<act>、<act:0.4> 会盖过参数。"""
    spoken, tag_mood, tag_cues = _parse_tags(str(text or ""))
    chosen_mood = tag_mood or (mood if mood in MOODS else None) or _guess_mood(spoken)
    chosen_lang = lang if lang in VOICES else detect_lang(spoken)
    chosen_cues = tag_cues or _explicit_cues(cues) or _guess_cues(spoken, chosen_mood)
    from viseme import mouth_track

    timeline = []
    for cue in chosen_cues[:4]:
        spec = ACTS[cue["act"]]
        timeline.append({
            "at": cue["at"],
            "act": cue["act"],
            "duration": spec["duration"],
            "tracks": spec["tracks"],
        })
    pose = dict(MOODS[chosen_mood])
    pose.pop("ParamMouthOpenY", None)
    pose.pop("ParamMouthForm", None)
    chosen_voice = resolve_voice(voice, chosen_lang, pitch, rate)
    return {
        "say": spoken,
        "mood": chosen_mood,
        "moodPose": pose,
        "mouthBias": MOUTH_BIAS.get(chosen_mood, 0.0),
        "cues": timeline,
        "mouth": mouth_track(spoken, chosen_lang),
        "lang": chosen_lang,
        "voice": chosen_voice["voice"],
        "voiceId": chosen_voice["id"],
        "voiceLabel": chosen_voice["label"],
        "pitch": chosen_voice["pitch"],
        "rate": chosen_voice["rate"],
        "estimate": round(estimate_seconds(spoken, chosen_lang), 2),
    }
