# -*- coding: utf-8 -*-
"""内置大肥鱼，并把模型目录里的动作、表情登记进 model3。

作者配布的 model3 没有 Motions / Expressions。
Cubism 和 pixi-live2d-display 只播放写进 FileReferences 的文件，
所以这里按目录补一份索引。用户再丢进新的 motion3 / exp3，刷新后也会出现。
模型文件留在本地，不作为引擎的再分发内容。
"""

from pathlib import Path
import hashlib
import json
import platform
import re
import subprocess

ROOT = Path(__file__).resolve().parent
MODEL_ID = "deepseek-chan"
MODEL_DIR = ROOT / "models" / "deepseek-chan"
MODEL_FILE = "大肥鱼.model3.json"
CDI_FILE = "大肥鱼.cdi3.json"
# 用户自己添加的模型只写在本机，不进仓库。
LIBRARY = ROOT / "models.local.json"
_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")

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


def _clips(folder: Path, suffix: str) -> list:
    """收集模型目录里的动作或表情。纹理和物理文件不是这个后缀。"""
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*" + suffix):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if ".." in rel.split("/"):
            continue
        name = path.name
        stem = name[:-len(suffix)] if name.endswith(suffix) else path.stem
        if not stem:
            continue
        found.append((stem, rel))
    found.sort(key=lambda item: item[1])
    return found


def motion_list_in(folder: Path) -> list:
    """待机用 Cubism 约定的 Idle 组名，其余一组一个文件，名字就是文件名。"""
    items = []
    for stem, rel in _clips(folder, ".motion3.json"):
        group = "Idle" if stem.lower() == "idle" else stem
        items.append({"id": group, "label": "待机" if group == "Idle" else stem, "file": rel})
    return items


def expression_list_in(folder: Path) -> list:
    return [{"id": stem, "label": stem, "file": rel} for stem, rel in _clips(folder, ".exp3.json")]


def motion_list() -> list:
    return motion_list_in(MODEL_DIR)


def expression_list() -> list:
    return expression_list_in(MODEL_DIR)


def _record_folder(model_id=None) -> Path:
    if model_id:
        for record in load_library():
            if record.get("id") == model_id:
                return Path(record["dir"])
    return MODEL_DIR


def known_motions(model_id=None) -> set:
    return {item["id"] for item in motion_list_in(_record_folder(model_id))}


def known_expressions(model_id=None) -> set:
    return {item["id"] for item in expression_list_in(_record_folder(model_id))}


def ensure_index_in(folder: Path, model_file: str) -> None:
    """把目录里还没登记的动作和表情补进 model3。已经写过的分组保持不动。"""
    path = folder / model_file
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    refs = data.setdefault("FileReferences", {})
    existing = refs.get("Motions") if isinstance(refs.get("Motions"), dict) else {}
    motions = {}
    known_files = set()
    for group, items in existing.items():
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            if isinstance(item, dict) and item.get("File"):
                known_files.add(str(item["File"]).replace("\\", "/"))
                kept.append(item)
        if kept:
            motions[group] = kept
    for item in motion_list_in(folder):
        if item["file"] in known_files:
            continue
        motions.setdefault(item["id"], []).append({
            "File": item["file"],
            "FadeInTime": 0.3,
            "FadeOutTime": 0.3,
        })
        known_files.add(item["file"])
    expressions = refs.get("Expressions") if isinstance(refs.get("Expressions"), list) else []
    known_expr = set()
    kept_expr = []
    for item in expressions:
        if isinstance(item, dict) and item.get("File"):
            known_expr.add(str(item["File"]).replace("\\", "/"))
            kept_expr.append(item)
    for item in expression_list_in(folder):
        if item["file"] in known_expr:
            continue
        kept_expr.append({"Name": item["id"], "File": item["file"]})
        known_expr.add(item["file"])
    if refs.get("Motions") == motions and refs.get("Expressions") == kept_expr:
        return
    refs["Motions"] = motions
    refs["Expressions"] = kept_expr
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_index() -> None:
    """给内置大肥鱼补动作索引。"""
    ensure_index_in(MODEL_DIR, MODEL_FILE)


def _builtin_record() -> dict:
    return {
        "id": MODEL_ID,
        "name": "大肥鱼",
        "dir": str(MODEL_DIR),
        "model": MODEL_FILE,
        "cdi": CDI_FILE,
        "freedom": True,
        "builtin": True,
        "author": "神宫凉子",
        "authorUrl": "https://space.bilibili.com/13737731",
        "rigger": "霓虹猫_NeonNeko",
    }


def load_library() -> list:
    """读本机模型库。内置大肥鱼始终在，高自由度开关按用户保存的来。"""
    saved = []
    if LIBRARY.is_file():
        try:
            raw = json.loads(LIBRARY.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict) and isinstance(raw.get("models"), list):
            saved = [item for item in raw["models"] if isinstance(item, dict)]
    records = []
    seen = set()
    fish_freedom = True
    for item in saved:
        if item.get("builtin") or item.get("id") == MODEL_ID:
            fish_freedom = bool(item.get("freedom", True))
            continue
        model_id = str(item.get("id") or "")
        folder = str(item.get("dir") or "")
        model_file = str(item.get("model") or "")
        if not _ID_OK.match(model_id) or not folder or not model_file:
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        records.append({
            "id": model_id,
            "name": str(item.get("name") or model_id)[:40],
            "dir": folder,
            "model": model_file,
            "cdi": str(item.get("cdi") or ""),
            "freedom": bool(item.get("freedom")),
            "builtin": False,
        })
    fish = _builtin_record()
    fish["freedom"] = fish_freedom
    return [fish] + records


def _write_library(records: list) -> None:
    LIBRARY.write_text(
        json.dumps({"models": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def public_model(record: dict) -> dict:
    """给页面的一条模型。路径只在本机使用。"""
    folder = Path(record["dir"])
    model_file = record["model"]
    return {
        "id": record["id"],
        "name": record["name"],
        "mark": (record["name"] or "?")[:1],
        "model": model_file,
        "cdi": record.get("cdi") or "",
        "ready": (folder / model_file).is_file(),
        "freedom": bool(record.get("freedom")),
        "builtin": bool(record.get("builtin")),
        "path": str((folder / model_file).resolve()) if (folder / model_file).is_file() else str(folder / model_file),
        "author": record.get("author") or "",
        "authorUrl": record.get("authorUrl") or "",
        "rigger": record.get("rigger") or "",
    }


def model_map() -> dict:
    """给静态文件用。编号对应目录，目录本身不进 URL。"""
    found = {}
    for record in load_library():
        found[record["id"]] = {
            "id": record["id"],
            "name": record["name"],
            "mark": (record["name"] or "?")[:1],
            "dir": Path(record["dir"]),
            "model": record["model"],
            "cdi": record.get("cdi") or "",
            "freedom": bool(record.get("freedom")),
        }
    return found


def freedom_models() -> list:
    """高自由度允许切换的模型。文件不在的不算。"""
    return [public_model(record) for record in load_library() if record.get("freedom") and (Path(record["dir"]) / record["model"]).is_file()]


def freedom_ids() -> set:
    return {item["id"] for item in freedom_models()}


def picker_label() -> str:
    system = platform.system()
    if system == "Windows":
        return "从资源管理器选择"
    if system == "Darwin":
        return "从访达选择"
    return "选择模型文件"


def inspect_model(model_path) -> dict:
    """确认这是一份 model3，并找出同目录的 cdi。"""
    path = Path(model_path).expanduser().resolve()
    if not path.is_file() or not path.name.endswith(".model3.json"):
        raise ValueError("请选择 Live2D 的 model3.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("这个 model3.json 读不了")
    refs = data.get("FileReferences") if isinstance(data, dict) else None
    if not isinstance(refs, dict) or not refs.get("Moc"):
        raise ValueError("这份 json 不是 Live2D 模型")
    folder = path.parent
    cdi = str(refs.get("DisplayInfo") or "")
    if cdi and not (folder / cdi).is_file():
        cdi = ""
    if not cdi:
        found = list(folder.glob("*.cdi3.json"))
        cdi = found[0].name if found else ""
    return {
        "name": path.name[: -len(".model3.json")] or path.stem,
        "dir": str(folder),
        "model": path.name,
        "cdi": cdi,
        "path": str(path),
        "motions": len(motion_list_in(folder)),
        "expressions": len(expression_list_in(folder)),
    }


def _new_id(name: str, folder: Path) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = "model"
    digest = hashlib.sha1(str(folder).encode("utf-8")).hexdigest()[:8]
    model_id = (slug[:24] + "-" + digest)[:49]
    if not _ID_OK.match(model_id):
        model_id = "model-" + digest
    taken = {item["id"] for item in load_library()}
    if model_id not in taken:
        return model_id
    return "model-" + digest


def save_model(model_path, name=None, freedom=False) -> dict:
    """把一份模型记进本机模型库。同一路径再次保存只更新名字和开关。"""
    info = inspect_model(model_path)
    records = load_library()
    target = None
    for record in records:
        if Path(record["dir"]).resolve() == Path(info["dir"]).resolve() and record["model"] == info["model"]:
            target = record
            break
    if target is None:
        target = {
            "id": _new_id(info["name"], Path(info["dir"])),
            "name": info["name"],
            "dir": info["dir"],
            "model": info["model"],
            "cdi": info["cdi"],
            "freedom": False,
            "builtin": False,
        }
        records.append(target)
    if name and not target.get("builtin"):
        target["name"] = str(name).strip()[:40] or target["name"]
    if target.get("builtin"):
        target["name"] = "大肥鱼"
        target["freedom"] = bool(freedom)
    else:
        target["cdi"] = info["cdi"]
        target["freedom"] = bool(freedom)
    ensure_index_in(Path(target["dir"]), target["model"])
    _write_library(records)
    return public_model(target)


def set_freedom(model_id, allowed) -> dict:
    records = load_library()
    found = None
    for record in records:
        if record["id"] == model_id:
            record["freedom"] = bool(allowed)
            found = record
            break
    if found is None:
        raise ValueError("没有这套模型")
    _write_library(records)
    return public_model(found)


def remove_model(model_id) -> None:
    records = load_library()
    kept = []
    found = False
    for record in records:
        if record["id"] != model_id:
            kept.append(record)
            continue
        found = True
        if record.get("builtin"):
            raise ValueError("内置的大肥鱼不能从库里删掉")
    if not found:
        raise ValueError("没有这套模型")
    _write_library(kept)


def _pick_model_file() -> str:
    """弹出系统的文件窗口。Windows 用资源管理器，macOS 用访达。"""
    system = platform.system()
    if system == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog; "
            "$dialog.Filter = 'Live2D (*.model3.json)|*.model3.json'; "
            "$dialog.Title = '选择 Live2D 模型'; "
            "$dialog.CheckFileExists = $true; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
            "[Console]::Out.WriteLine($dialog.FileName) }"
        )
        proc = subprocess.run(
            ["powershell", "-STA", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=300,
        )
        text = proc.stdout.decode("utf-8", "replace").strip()
        return text.splitlines()[-1].strip() if text else ""
    if system == "Darwin":
        proc = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose file with prompt "选择 Live2D 的 model3.json")'],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()
    proc = subprocess.run(
        ["zenity", "--file-selection", "--title=选择 Live2D 模型", "--file-filter=*.model3.json"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def browse() -> dict:
    """打开选择窗口。取消时不报错。"""
    try:
        chosen = _pick_model_file()
    except (OSError, subprocess.TimeoutExpired):
        return {"cancelled": False, "error": "没有打开选择窗口"}
    if not chosen:
        return {"cancelled": True}
    try:
        return {"cancelled": False, "preview": inspect_model(chosen)}
    except ValueError as exc:
        return {"cancelled": False, "error": str(exc)}


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


def clean_stage(raw, allowed_models=None) -> dict | None:
    """自由操控的舞台指令。只留白名单字段，避免模型改到密钥或任意地址。

    allowed_models 是允许在高自由度下切换的模型 id。不在里面的 modelId 会被丢掉。
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    if raw.get("mode") in ("live", "pet"):
        out["mode"] = raw["mode"]
    wanted = str(raw.get("modelId") or "").strip()
    if wanted and allowed_models and wanted in allowed_models:
        out["modelId"] = wanted
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


def public_catalog(model_id=None) -> dict:
    """给页面和口播提示词。动作按当前模型来，高自由度名单单独给。"""
    folder = _record_folder(model_id)
    current = None
    for record in load_library():
        if model_id and record["id"] == model_id:
            current = record
            break
        if not model_id and record["id"] == MODEL_ID:
            current = record
            break
    if current is None:
        current = _builtin_record()
        folder = Path(current["dir"])
    if (folder / current["model"]).is_file():
        ensure_index_in(folder, current["model"])
    motions = [{"id": item["id"], "label": item["label"]} for item in motion_list_in(folder)]
    expressions = [{"id": item["id"], "label": item["label"]} for item in expression_list_in(folder)]
    return {
        "model": public_model(current),
        "motions": motions,
        "expressions": expressions,
        "models": [public_model(record) for record in load_library()],
        "freedomModels": [item["id"] for item in freedom_models()],
        "picker": picker_label(),
        "toggles": list(TOGGLE_IDS),
        "backdropPresets": BACKDROP_PRESETS,
    }
