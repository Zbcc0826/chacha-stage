# -*- coding: utf-8 -*-
"""连接用户自己的 DeepSeek Harness。

密钥来自用户自己填写，写入 dsh 主目录的 .env，或来自环境变量。
本模块不读取引擎目录下的 keys 文件，也不把密钥返回给页面。
"""

from pathlib import Path
import os
import re

ROOT = Path(__file__).resolve().parent
LINK_FILE = ROOT / "dsh.local"
DEFAULT_HOME = ROOT.parent / "deepseek-harness" / "dsh-home"


def _parse_env_file(path: Path) -> dict:
    found = {}
    if not path.is_file():
        return found
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value:
            found[name] = value
    return found


def home() -> Path:
    """dsh 主目录。页面可以改，改完写在 dsh.local，只存目录。"""
    chosen = os.environ.get("DSH_HOME", "").strip()
    if not chosen:
        chosen = _parse_env_file(LINK_FILE).get("home", "").strip()
    return Path(chosen) if chosen else DEFAULT_HOME


def save_home(folder: str) -> Path:
    """只接受本机已有目录。拒绝顺手把密钥写进来。"""
    path = Path(str(folder or "").strip()).expanduser()
    if not path.is_dir():
        raise RuntimeError("dsh 目录不存在")
    LINK_FILE.write_text("home=%s\n" % path, encoding="utf-8")
    return path


def _clean_key(raw: str) -> str:
    """只接受 DeepSeek 这种 sk- 开头的密钥。错误信息里不带回原文。"""
    key = str(raw or "").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_\-]{16,180}", key):
        raise RuntimeError("密钥格式不对。请填写 DeepSeek 官方发给你的密钥。")
    return key


def _write_env_key(path: Path, key: str) -> None:
    """改 .env 里的 DEEPSEEK_API_KEY，其它行原样留下。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replaced = False
    kept = []
    for line in lines:
        if line.strip().startswith("DEEPSEEK_API_KEY="):
            if not replaced:
                kept.append("DEEPSEEK_API_KEY=" + key)
                replaced = True
            continue
        kept.append(line)
    if not replaced:
        if kept and kept[-1] != "":
            kept.append("")
        kept.append("DEEPSEEK_API_KEY=" + key)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def save_api_key(raw: str) -> None:
    """把用户自己的密钥写进 dsh 主目录的 .env。不打印、不回传密钥。"""
    key = _clean_key(raw)
    folder = home()
    if not folder.is_dir():
        raise RuntimeError("dsh 目录不存在")
    _write_env_key(folder / ".env", key)
    os.environ["DEEPSEEK_API_KEY"] = key


def api_key() -> str:
    """用户 dsh 的密钥。没有就返回空字符串，不回退到引擎自带的 keys。"""
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env.startswith("sk-") and len(env) > 12:
        return env
    for candidate in (home() / ".env", home().parent / ".env"):
        value = _parse_env_file(candidate).get("DEEPSEEK_API_KEY", "").strip()
        if value.startswith("sk-") and len(value) > 12:
            return value
    return ""


def key_ready() -> bool:
    return bool(api_key())


def status() -> dict:
    """给页面的连接状态。不包含密钥，也不包含余额数字。"""
    folder = home()
    cli = ROOT.parent / "deepseek-harness" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    return {
        "home": str(folder),
        "homeReady": folder.is_dir(),
        "cliReady": cli.is_file(),
        "keyReady": key_ready(),
        "hint": "血条按官方余额计算，100 元满血。还没有密钥，所以余额查不了。请在下面填你自己的 DeepSeek 密钥。",
    }
