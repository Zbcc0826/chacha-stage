# -*- coding: utf-8 -*-
"""内置大肥鱼，并把模型目录里的动作、表情登记进 model3。

作者配布的 model3 没有 Motions / Expressions。
Cubism 和 pixi-live2d-display 只播放写进 FileReferences 的文件，
所以这里按目录补一份索引。用户再丢进新的 motion3 / exp3，刷新后也会出现。
模型文件留在本地，不作为引擎的再分发内容。
"""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
MODEL_ID = "deepseek-chan"
MODEL_DIR = ROOT / "models" / "deepseek-chan"
MODEL_FILE = "大肥鱼.model3.json"
CDI_FILE = "大肥鱼.cdi3.json"

# 直播软件里常见的可抠像底色。品红是当前舞台默认色。
BACKDROP_PRESETS = [
    {"id": "magenta", "label": "品红", "color": "#ff00ff"},
    {"id": "green", "label": "绿幕", "color": "#00b140"},
    {"id": "blue", "label": "蓝幕", "color": "#0000ff"},
    {"id": "black", "label": "纯黑", "color": "#000000"},
    {"id": "white", "label": "纯白", "color": "#ffffff"},
    {"id": "gray", "label": "深灰", "color": "#1a1a1a"},
]

# 自由操控打开时，模型和页面共用的开关名。
TOGGLE_IDS = (
    "hud", "danmaku", "physics", "breath", "sway", "blink", "mouse", "live", "captions",
)


def ready() -> bool:
    return (MODEL_DIR / MODEL_FILE).is_file() and (MODEL_DIR / "大肥鱼.moc3").is_file()


def spec() -> dict:
    return {
        "id": MODEL_ID,
        "name": "大肥鱼",
        "mark": "鱼",
        "dir": MODEL_DIR,
        "model": MODEL_FILE,
        "cdi": CDI_FILE,
        "author": "神宫凉子",
        "authorUrl": "https://space.bilibili.com/13737731",
        "rigger": "霓虹猫_NeonNeko",
    }


def _clips(suffix: str) -> list:
    """收集模型目录里的动作或表情。纹理和物理文件不是这个后缀。"""
    if not MODEL_DIR.is_dir():
        return []
    found = []
    for path in MODEL_DIR.rglob("*" + suffix):
        if not path.is_file():
            continue
        rel = path.relative_to(MODEL_DIR).as_posix()
        if ".." in rel.split("/"):
            continue
        name = path.name
        stem = name[:-len(suffix)] if name.endswith(suffix) else path.stem
        if not stem:
            continue
        found.append((stem, rel))
    found.sort(key=lambda item: item[1])
    return found


def motion_list() -> list:
    """待机用 Cubism 约定的 Idle 组名，其余一组一个文件，名字就是文件名。"""
    items = []
    for stem, rel in _clips(".motion3.json"):
        group = "Idle" if stem.lower() == "idle" else stem
        items.append({"id": group, "label": "待机" if group == "Idle" else stem, "file": rel})
    return items


def expression_list() -> list:
    return [{"id": stem, "label": stem, "file": rel} for stem, rel in _clips(".exp3.json")]


def ensure_index() -> None:
    """把目录里的动作和表情写回 model3。内容没变就不碰文件。"""
    path = MODEL_DIR / MODEL_FILE
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    refs = data.setdefault("FileReferences", {})
    motions = {}
    for item in motion_list():
        motions.setdefault(item["id"], []).append({
            "File": item["file"],
            "FadeInTime": 0.3,
            "FadeOutTime": 0.3,
        })
    expressions = [{"Name": item["id"], "File": item["file"]} for item in expression_list()]
    if refs.get("Motions") == motions and refs.get("Expressions") == expressions:
        return
    refs["Motions"] = motions
    refs["Expressions"] = expressions
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def known_motions() -> set:
    return {item["id"] for item in motion_list()}


def known_expressions() -> set:
    return {item["id"] for item in expression_list()}


def clean_name(value, allowed: set) -> str:
    text = str(value or "").strip()
    if text in allowed and len(text) < 80:
        return text
    return ""


def _num(value, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return max(low, min(high, number))


def _css(text: str) -> str:
    """留下给本页 <style> 用的片段。截断，并去掉会跳出样式表的写法。"""
    raw = str(text or "")[:6000]
    lowered = raw.lower()
    blocked = ("</", "@import", "javascript:", "expression(", "behavior:", "-moz-binding", "@charset")
    if any(token in lowered for token in blocked):
        return ""
    return raw.strip()


def clean_stage(raw) -> dict | None:
    """自由操控的舞台指令。只留白名单字段，避免模型改到密钥或任意地址。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    if raw.get("mode") in ("live", "pet"):
        out["mode"] = raw["mode"]
    model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
    placed = {}
    for key, low, high in (("x", -2000, 2000), ("y", -2000, 2000), ("scale", 0.25, 4)):
        if key in model:
            number = _num(model.get(key), low, high)
            if number is not None:
                placed[key] = number
    if placed:
        out["model"] = placed
    background = raw.get("background") if isinstance(raw.get("background"), dict) else {}
    paint = {}
    color = str(background.get("color") or "")
    if len(color) in (4, 7) and color.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in color[1:]):
        paint["color"] = color.lower()
    for key, low, high in (("imageOpacity", 0, 1), ("imageScale", 0.2, 3)):
        if key in background:
            number = _num(background.get(key), low, high)
            if number is not None:
                paint[key] = number
    if paint:
        out["background"] = paint
    widgets = raw.get("widgets") if isinstance(raw.get("widgets"), dict) else {}
    cleaned = {}
    for name in ("hud", "danmaku"):
        item = widgets.get(name)
        if not isinstance(item, dict):
            continue
        one = {}
        if isinstance(item.get("show"), bool):
            one["show"] = item["show"]
        for key, low, high in (("x", 0, 4000), ("y", 0, 4000), ("scale", 0.6, 2), ("z", 1, 40)):
            if key in item:
                number = _num(item.get(key), low, high)
                if number is not None:
                    one[key] = number
        if name == "hud" and item.get("style") in ("jrpg", "action", "moba", "pixel"):
            one["style"] = item["style"]
        if name == "danmaku" and "count" in item:
            number = _num(item.get("count"), 1, 20)
            if number is not None:
                one["count"] = int(number)
        if one:
            cleaned[name] = one
    if cleaned:
        out["widgets"] = cleaned
    toggles = raw.get("toggles") if isinstance(raw.get("toggles"), dict) else {}
    flags = {}
    for name in TOGGLE_IDS:
        if isinstance(toggles.get(name), bool):
            flags[name] = toggles[name]
    if flags:
        out["toggles"] = flags
    css = _css(raw.get("css") or "")
    if css:
        out["css"] = css
    return out or None


def public_catalog() -> dict:
    """给页面和口播提示词。不含磁盘路径。"""
    ensure_index()
    motions = [{"id": item["id"], "label": item["label"]} for item in motion_list()]
    expressions = [{"id": item["id"], "label": item["label"]} for item in expression_list()]
    return {
        "model": {
            "id": MODEL_ID,
            "name": "大肥鱼",
            "ready": ready(),
            "author": "神宫凉子",
            "authorUrl": "https://space.bilibili.com/13737731",
            "rigger": "霓虹猫_NeonNeko",
        },
        "motions": motions,
        "expressions": expressions,
        "toggles": list(TOGGLE_IDS),
        "backdropPresets": BACKDROP_PRESETS,
    }
