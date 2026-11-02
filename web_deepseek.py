# -*- coding: utf-8 -*-
"""用本机 Chrome 打开 DeepSeek 网页聊天，省掉 API token。

Cursor 的桌面操作帮手这次启动失败，内置浏览器也会被网站判成异常环境。
所以这里改用本机安装的 Chrome，配置单独放在 browser-profile，不碰日常浏览器。

条件同时满足才走网页，否则抛 WebDeepSeekUnavailable，调用方退回官方 API：
已安装 playwright 和 Chrome、配置里曾经登录成功、当前不是登录页、
没有「异常使用环境」提示、页面上找得到输入框。
"""

import sys
import threading
import time
from pathlib import Path

from llm import _extract_json, system_prompt

CHAT_URL = "https://chat.deepseek.com/"
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "browser-profile"
READY = PROFILE / ".ready"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
_LOCK = threading.Lock()

# 在页面里看登录状态、输入框和最后一条回答。类名会变，所以多试几种。
_PAGE_STATE = """
() => {
  const text = document.body ? document.body.innerText.slice(0, 4000) : "";
  const blocked = /Abnormal usage|异常使用环境/.test(text);
  const login = /sign_in/.test(location.href) || /Phone number|手机号|密码登录/.test(text);
  const areas = [...document.querySelectorAll("textarea, [contenteditable='true']")];
  const composer = areas.find((el) => {
    const box = el.getBoundingClientRect();
    return box.width > 80 && box.height > 16 && box.bottom > window.innerHeight * 0.45;
  }) || null;
  const blocks = [...document.querySelectorAll(".ds-markdown, .markdown, [class*='markdown']")];
  const last = blocks.length ? blocks[blocks.length - 1].innerText.trim() : "";
  if (composer) composer.setAttribute("data-chacha-composer", "1");
  return {
    blocked,
    login,
    hasComposer: !!composer,
    last,
    href: location.href,
  };
}
"""


class WebDeepSeekUnavailable(RuntimeError):
    """网页这条路现在走不通，应该退回 API。"""


def web_ready():
    """只表示以前成功打开过输入框，不代表这一次一定还能用。"""
    return READY.is_file() and CHROME.is_file()


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WebDeepSeekUnavailable("还没安装 playwright，网页通道先不用") from exc
    if not CHROME.is_file():
        raise WebDeepSeekUnavailable("没有找到本机 Chrome")
    return sync_playwright


def _launch(playwright):
    """打开单独的 Chrome 配置。不开自动化横幅，减少被当成异常环境的机会。"""
    PROFILE.mkdir(exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(PROFILE),
        executable_path=str(CHROME),
        headless=False,
        viewport={"width": 1100, "height": 820},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )


def _state(page):
    """页面正在跳转时先当作还没登录，下一秒再看。"""
    try:
        return page.evaluate(_PAGE_STATE)
    except Exception:
        href = ""
        try:
            href = page.url
        except Exception:
            href = ""
        return {"blocked": False, "login": True, "hasComposer": False, "last": "", "href": href}


def _logged_in(state):
    """输入框出现、而且不在登录页，才算网页通道可用。"""
    if state.get("blocked"):
        return False
    href = state.get("href") or ""
    if "sign_in" in href:
        return False
    return bool(state.get("hasComposer"))


def _mark_logged_out():
    """登录掉了就清掉标记，避免每一句都再打开一次网页。"""
    if READY.is_file():
        READY.unlink(missing_ok=True)


def _reject_if_blocked(state):
    if state.get("blocked"):
        _mark_logged_out()
        raise WebDeepSeekUnavailable("网页提示当前环境异常，已改走 API")
    if not _logged_in(state):
        _mark_logged_out()
        raise WebDeepSeekUnavailable("网页版还没登录")


def open_login(wait_seconds=300):
    """打开登录页并等到输入框出现。密码由人自己输入，脚本不记录。"""
    sync_playwright = _require_playwright()
    if not _LOCK.acquire(blocking=False):
        raise WebDeepSeekUnavailable("已经有一个网页窗口开着")
    try:
        with sync_playwright() as playwright:
            context = _launch(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=30000)
            deadline = time.time() + wait_seconds
            print("请在弹出的 Chrome 里登录 DeepSeek。登录成功后窗口会自己关掉。", flush=True)
            while time.time() < deadline:
                state = _state(page)
                if state.get("blocked"):
                    raise WebDeepSeekUnavailable("网页提示当前环境异常")
                if _logged_in(state):
                    READY.write_text("ok", encoding="utf-8")
                    print("网页登录已记下。", flush=True)
                    context.close()
                    return True
                page.wait_for_timeout(1000)
            raise WebDeepSeekUnavailable("等待登录超时")
    finally:
        _LOCK.release()


def _prompt(user_text, history, lang):
    """网页没有单独的系统角色，把表演规则和最近几句写进同一条消息。"""
    lines = [system_prompt(), ""]
    for item in (history or [])[-6:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role == "user" and content:
            lines.append("用户：" + content[:500])
        elif role == "assistant" and content:
            lines.append("你：" + content[:500])
    spoken = str(user_text or "").strip()
    if lang in ("zh", "ja", "en"):
        spoken += "\n请使用语言：" + {"zh": "中文", "ja": "日语", "en": "英语"}[lang]
    lines.append("用户：" + spoken[:2000])
    lines.append("只输出 JSON。")
    text = "\n".join(lines)
    return text[:6000]


def _focus_composer(page):
    composer = page.locator("[data-chacha-composer='1']")
    if composer.count() == 0:
        raise WebDeepSeekUnavailable("找不到网页输入框")
    composer.first.click(timeout=5000)
    return composer.first


def _set_web_search(page, enabled):
    """拨网页输入框旁的「智能搜索」。找不到开关时返回 missing，不乱点别的按钮。"""
    return page.evaluate(
        """(enabled) => {
          const button = [...document.querySelectorAll('.ds-toggle-button')]
            .find((el) => (el.innerText || '').includes('智能搜索'));
          if (!button) return 'missing';
          const on = button.classList.contains('ds-toggle-button--selected');
          if (on !== enabled) button.click();
          return enabled ? 'on' : 'off';
        }""",
        enabled,
    )


def _send(text, wait=False, search=False):
    """把一段原文发给网页对话，返回最后一条回答。调用方自己解析。

    wait 为真时排队等上一轮网页对话结束，避免标题请求挤掉口播。
    """
    if not web_ready():
        raise WebDeepSeekUnavailable("网页通道还没登录")
    sync_playwright = _require_playwright()
    if not _LOCK.acquire(timeout=120 if wait else 0):
        raise WebDeepSeekUnavailable("网页窗口正忙")
    try:
        with sync_playwright() as playwright:
            context = _launch(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
                state = _state(page)
                _reject_if_blocked(state)
                before = state.get("last") or ""
                _focus_composer(page)
                search_state = _set_web_search(page, search)
                if search and search_state == "missing":
                    raise WebDeepSeekUnavailable("网页上没有智能搜索开关")
                if search:
                    page.wait_for_timeout(400)
                page.keyboard.insert_text(str(text or "")[:12000])
                page.keyboard.press("Enter")
                return _wait_reply(page, before)
            finally:
                context.close()
    finally:
        _LOCK.release()


def complete_web(prompt, search=False):
    """Harness 的模型请求走这里。search 为真时先打开网页自带的智能搜索。"""
    return _send(prompt, wait=True, search=search)


def ask_web(user_text, history=None, lang="auto"):
    """在网页里问一句，解析成和 API 一样的 say、mood、cues。"""
    reply = _send(_prompt(user_text, history, lang))
    data = _extract_json(reply)
    say = str(data.get("say") or "").strip()
    if not say:
        raise WebDeepSeekUnavailable("网页回答里没有要说的话")
    return {
        "say": say,
        "mood": data.get("mood"),
        "cues": data.get("cues") if isinstance(data.get("cues"), list) else [],
    }


def _wait_reply(page, before):
    """等到最后一条回答出现，并且连续两秒不再变长。"""
    stable = ""
    stable_since = 0.0
    deadline = time.time() + 90
    while time.time() < deadline:
        state = _state(page)
        if state.get("blocked"):
            raise WebDeepSeekUnavailable("网页提示当前环境异常")
        last = state.get("last") or ""
        if last and last != before:
            if last == stable:
                if time.time() - stable_since >= 2:
                    return last
            else:
                stable = last
                stable_since = time.time()
        page.wait_for_timeout(400)
    if stable and stable != before:
        return stable
    raise WebDeepSeekUnavailable("网页没有在时限内答完")


def main(argv):
    """命令行：--login 等人工登录，--check 只看现在能不能用。"""
    command = argv[1] if len(argv) > 1 else "--check"
    try:
        if command == "--login":
            open_login()
            return 0
        if command == "--check":
            print("webReady", web_ready())
            return 0 if web_ready() else 2
        result = ask_web(command)
        print(result["say"])
        print(result.get("mood"), result.get("cues"))
        return 0
    except WebDeepSeekUnavailable as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
