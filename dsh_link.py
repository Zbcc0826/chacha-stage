# -*- coding: utf-8 -*-
"""连接用户自己的 DeepSeek Harness。

密钥只来自用户的 dsh：进程环境变量，或 dsh 主目录里的 .env。
本模块不读取引擎目录下的 keys 文件。
"""

from pathlib import Path
import os

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
        "hint": "密钥放在你自己的 dsh 环境变量 DEEPSEEK_API_KEY，或 dsh 主目录的 .env 里。引擎不保存、也不使用作者的密钥。",
    }
