# -*- coding: utf-8 -*-
"""桌宠浮窗。

透明、无边框、置顶，不进任务栏。空白处鼠标穿透，点在角色上才能拖动和打开控件。
直播间页面仍留在浏览器里当控制台。
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import sys
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8787
# 窗口标题单独起，避免按标题找句柄时撞上浏览器标签。
TITLE = "大肥鱼桌宠"
WIDTH = 520
HEIGHT = 760
# 和 server.py 用同一个名字。占住就不许再开第二个桌宠。
PET_MUTEX_NAME = "Local\\ChachaStageDesktopPet"
PET_PID = Path(__file__).resolve().with_name("pet-window.pid")
_PET_MUTEX = None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
# 必须在 WebView2 创建交换链之前加上，网页的透明像素才会透出桌面。
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _win32():
    """单独的 user32 实例。不要改全局 windll 的参数类型，免得碰到 pywebview 自己的调用。"""
    return ctypes.WinDLL("user32", use_last_error=True)


def _handle32(hwnd):
    """跟 pywebview 一样用 32 位句柄改扩展样式。扩展样式本身只有 32 位。"""
    value = int(hwnd) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    return value


def _read_style(hwnd):
    return _win32().GetWindowLongW(_handle32(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF


def _write_style(hwnd, style):
    """写扩展样式。调用方必须在界面线程上，否则会和 WebView2 互相等死。"""
    user = _win32()
    ctypes.set_last_error(0)
    user.SetWindowLongW(_handle32(hwnd), GWL_EXSTYLE, int(style) & 0xFFFFFFFF)
    err = ctypes.get_last_error()
    now = user.GetWindowLongW(_handle32(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF
    return now, err


def _chrome_style(hwnd):
    """读一下创建后的扩展样式，确认按像素透明有没有留下来。"""
    now = _read_style(hwnd)
    print("透明样式 %s" % hex(now), flush=True)
    return now


def _glass(hwnd):
    """把客户区交给桌面合成，透明像素才不会被填成底色。"""
    margins = _MARGINS(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(_handle32(hwnd), ctypes.byref(margins))


def _pass_style(hwnd, enabled):
    """只切换穿透位。创建后不能再改按像素透明，带上那个位整次调用都会失败。"""
    style = _read_style(hwnd)
    style &= ~WS_EX_LAYERED
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    now, err = _write_style(hwnd, style)
    if err:
        print("穿透样式 %s 错误 %s" % (hex(now), err), flush=True)


def _dpi_scale():
    """系统缩放。工作区坐标是物理像素，pywebview 还会再乘一次。"""
    try:
        ctypes.windll.user32.GetDpiForSystem.restype = ctypes.c_uint
        dpi = ctypes.windll.user32.GetDpiForSystem() or 96
    except Exception:
        dpi = 96
    return max(dpi, 96) / 96


def _work_area():
    """工作区的逻辑像素宽高。pywebview 的窗口尺寸也用逻辑像素。"""
    rect = _RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
    scale = _dpi_scale()
    return (rect.right - rect.left) / scale, (rect.bottom - rect.top) / scale, scale


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _monitor_work(hwnd):
    """窗口所在显示器的工作区，避开任务栏。"""
    user = _win32()
    user.MonitorFromWindow.restype = ctypes.c_void_p
    user.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
    monitor = user.MonitorFromWindow(hwnd, 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return info.rcWork
    work = _RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)
    return work


def _resize_hwnd(hwnd, scale):
    """按倍率改窗口的物理尺寸，并守在当前显示器里。不再交给 pywebview，免得尺寸被 DPI 乘两次。"""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1
    scale = max(0.6, min(scale, 4))
    user = _win32()
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint,
    ]
    work = _monitor_work(hwnd)
    current = _RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(current)):
        return False
    max_w = max(160, work.right - work.left - 16)
    max_h = max(220, work.bottom - work.top - 16)
    width = int(round(WIDTH * scale))
    height = int(round(HEIGHT * scale))
    if width > max_w or height > max_h:
        fit = min(max_w / WIDTH, max_h / HEIGHT)
        width = int(round(WIDTH * fit))
        height = int(round(HEIGHT * fit))
    width = max(min(int(WIDTH * 0.6), max_w), min(width, max_w))
    height = max(min(int(HEIGHT * 0.6), max_h), min(height, max_h))
    # 角色锚点在窗口里的比例，和 web/app.js 的 layoutModel 一致。
    # 只在角色自己要离开屏幕时才挪窗口，窗口边缘可以伸出屏幕。
    old_w = max(1, current.right - current.left)
    old_h = max(1, current.bottom - current.top)
    anchor_x = current.left + old_w * 0.5
    anchor_y = current.top + old_h * 0.56
    anchor_x = min(max(anchor_x, work.left + 64), work.right - 64)
    anchor_y = min(max(anchor_y, work.top + 64), work.bottom - 64)
    x = int(round(anchor_x - width * 0.5))
    y = int(round(anchor_y - height * 0.56))
    user.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE)
    return True


def _work_origin():
    """把窗口放在工作区右下角，避开任务栏。返回值是逻辑像素。"""
    work_w, work_h, scale = _work_area()
    del work_w, work_h
    rect = _RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
    x = max(rect.left, rect.right - int(WIDTH * scale) - 48)
    y = max(rect.top, rect.bottom - int(HEIGHT * scale) - 24)
    return int(round(x / scale)), int(round(y / scale))


def _hwnd_of(window):
    """取出 WebView2 窗体句柄。"""
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return 0
    if hasattr(handle, "ToInt64"):
        return int(handle.ToInt64())
    return int(handle)


def _cursor_css(hwnd):
    """光标相对窗口的 CSS 像素。不在窗口里就返回空。"""
    user = _win32()
    user.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(_POINT)]
    try:
        user.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        user.GetDpiForWindow.restype = ctypes.c_uint
    except AttributeError:
        pass
    point = _POINT()
    rect = _RECT()
    if not user.GetCursorPos(ctypes.byref(point)):
        return None
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if not (rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom):
        return None
    if not user.ScreenToClient(hwnd, ctypes.byref(point)):
        return None
    dpi = 96
    try:
        dpi = user.GetDpiForWindow(hwnd) or 96
    except Exception:
        dpi = 96
    scale = max(dpi, 96) / 96
    return point.x / scale, point.y / scale


def _move_hwnd(hwnd, dx, dy):
    user = _win32()
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint,
    ]
    rect = _RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    user.SetWindowPos(
        hwnd, HWND_TOPMOST,
        int(rect.left + round(float(dx))),
        int(rect.top + round(float(dy))),
        0, 0,
        SWP_NOSIZE | SWP_NOACTIVATE,
    )
    return True


def _install_create_hook():
    """建窗时带上按像素透明。窗口一旦建好，系统就拒绝再改这个样式。"""
    global _CREATE_GATE, _CREATE_CALLBACK
    if _CREATE_GATE is not None:
        return
    import struct
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.VirtualAlloc.restype = ctypes.c_void_p
    kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_uint]
    kernel.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    kernel.FlushInstructionCache.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    target = ctypes.cast(user.CreateWindowExW, ctypes.c_void_p).value
    stolen_n = 17
    stolen = ctypes.string_at(target, stolen_n)
    # 只认当前这版 user32 的入口。对不上就不动，避免把建窗调用改坏。
    if stolen[:12] != bytes.fromhex("4c8bdc4881ec8800000033c0"):
        print("窗口入口变了，透明样式没加上", flush=True)
        return
    prototype = ctypes.WINFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    )

    def title_of(ptr):
        if not ptr or ptr < 0x10000:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        except Exception:
            return ""

    def detour(exstyle, cls, name, style, x, y, width, height, parent, menu, instance, param):
        if not parent and title_of(name) == TITLE:
            exstyle = (int(exstyle) | WS_EX_NOREDIRECTIONBITMAP | WS_EX_TOOLWINDOW) & 0xFFFFFFFF
            exstyle &= ~WS_EX_LAYERED
            print("建窗 %s" % hex(exstyle), flush=True)
        return _CREATE_GATE(exstyle, cls, name, style, x, y, width, height, parent, menu, instance, param)

    _CREATE_CALLBACK = prototype(detour)
    hook = ctypes.cast(_CREATE_CALLBACK, ctypes.c_void_p).value
    tramp = kernel.VirtualAlloc(None, 64, 0x3000, 0x40)
    code = stolen + b"\xFF\x25\x00\x00\x00\x00" + struct.pack("<Q", target + stolen_n)
    ctypes.memmove(tramp, code, len(code))
    _CREATE_GATE = prototype(tramp)
    patch = b"\xFF\x25\x00\x00\x00\x00" + struct.pack("<Q", hook) + b"\x90\x90\x90"
    old = ctypes.c_uint()
    if not kernel.VirtualProtect(target, stolen_n, 0x40, ctypes.byref(old)):
        print("窗口入口改不了", flush=True)
        return
    ctypes.memmove(target, patch, stolen_n)
    kernel.VirtualProtect(target, stolen_n, old.value, ctypes.byref(old))
    kernel.FlushInstructionCache(kernel.GetCurrentProcess(), target, stolen_n)


# 钩子和跳板要一直留着，不然建窗调用会跳进被回收的函数。
_CREATE_GATE = None
_CREATE_CALLBACK = None


def _allow_microphone(edge):
    """这一页自己打开语音输入时，允许它用麦克风。默认开关是关的。"""
    def ready(sender, args):
        try:
            if not args.IsSuccess:
                return
            from Microsoft.Web.WebView2.Core import (
                CoreWebView2PermissionKind,
                CoreWebView2PermissionState,
            )

            def allow(_sender, event):
                if event.PermissionKind == CoreWebView2PermissionKind.Microphone:
                    event.State = CoreWebView2PermissionState.Allow

            sender.CoreWebView2.PermissionRequested += allow
            edge._mic_allow = allow
        except Exception as exc:
            print("麦克风权限 %s" % exc, flush=True)

    edge.webview.CoreWebView2InitializationCompleted += ready


def _install_hook():
    """WebView2 建好前把窗体底色清掉，并确认透明样式还在。"""
    from webview.platforms import edgechromium

    original = edgechromium.EdgeChrome.__init__
    if getattr(original, "_chacha_hooked", False):
        return

    def hooked(self, form, window, cache_dir):
        try:
            form.ShowInTaskbar = False
            from System.Drawing import Color
            from System.Windows.Forms import ControlStyles
            form.SetStyle(ControlStyles.SupportsTransparentBackColor, True)
            form.BackColor = Color.Transparent
        except Exception as exc:
            print("窗体底色失败 %s" % exc, flush=True)
        try:
            hwnd = int(form.Handle.ToInt64())
            _glass(hwnd)
            _chrome_style(hwnd)
        except Exception as exc:
            print("透明样式失败 %s" % exc, flush=True)
        original(self, form, window, cache_dir)
        try:
            # 桌宠经常不在前台。关掉后台节流，口型和动作才会跟着声音走。
            props = self.webview.CreationProperties
            extra = " --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows"
            current = props.AdditionalBrowserArguments or ""
            if extra.strip() not in current:
                props.AdditionalBrowserArguments = current + extra
        except Exception as exc:
            print("后台节流 %s" % exc, flush=True)
        _allow_microphone(self)

    hooked._chacha_hooked = True
    edgechromium.EdgeChrome.__init__ = hooked


# 按住说话的全局按键。平时只看已经登记的组合是否都按下。
# 正在登记新按键时，才把按下的键名交给页面，仍然不拦截、不写日志。
_TALK_KEYS = []
_TALK_DOWN = False
_TALK_CAPTURE = False
_TALK_SEEN = set()
_TALK_HOOK = None
_TALK_HOOK_PROC = None
_VK_NAMES = {
    "Control": (0x11,),
    "Shift": (0x10,),
    "Alt": (0x12,),
    "Meta": (0x5B, 0x5C),
    "Space": (0x20,),
    "Enter": (0x0D,),
    "Tab": (0x09,),
    "Escape": (0x1B,),
    "Backspace": (0x08,),
    "Delete": (0x2E,),
    "Insert": (0x2D,),
    "Home": (0x24,),
    "End": (0x23,),
    "PageUp": (0x21,),
    "PageDown": (0x22,),
    "ArrowLeft": (0x25,),
    "ArrowUp": (0x26,),
    "ArrowRight": (0x27,),
    "ArrowDown": (0x28,),
}


def _vk_of(name):
    """把页面上的键名换成虚拟键码。认不出就返回空。"""
    if name in _VK_NAMES:
        return _VK_NAMES[name]
    if len(name) == 1 and "A" <= name.upper() <= "Z":
        return (ord(name.upper()),)
    if len(name) == 1 and "0" <= name <= "9":
        return (ord(name),)
    if len(name) >= 2 and name[0] == "F" and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 12:
            return (0x70 + number - 1,)
    return ()


def _name_of_vk(vk):
    """低级键盘钩子里的虚拟键码，换成和直播间页面相同的键名。"""
    vk = int(vk)
    named = {
        0x11: "Control", 0xA2: "Control", 0xA3: "Control",
        0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift",
        0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
        0x5B: "Meta", 0x5C: "Meta",
        0x20: "Space",
        0x0D: "Enter",
        0x09: "Tab",
        0x1B: "Escape",
        0x08: "Backspace",
        0x2E: "Delete",
        0x25: "ArrowLeft",
        0x26: "ArrowUp",
        0x27: "ArrowRight",
        0x28: "ArrowDown",
    }
    if vk in named:
        return named[vk]
    if 0x70 <= vk <= 0x7B:
        return "F%d" % (vk - 0x6F)
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    return ""


def _vk_from_hook(lparam):
    """从低级键盘钩子的参数里取出虚拟键码。"""
    if not lparam:
        return 0
    try:
        return int(ctypes.cast(lparam, ctypes.POINTER(ctypes.c_uint)).contents.value)
    except (ValueError, TypeError, ctypes.ArgumentError):
        return 0


def _talk_key_down(name):
    codes = _vk_of(name)
    if not codes:
        return False
    user = _win32()
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    return any(user.GetAsyncKeyState(code) & 0x8000 for code in codes)


def _normalize_talk_keys(text):
    keys = []
    for part in str(text or "").split(","):
        name = part.strip()
        if not name or name in keys or not _vk_of(name):
            continue
        keys.append(name)
        if len(keys) == 2:
            break
    return keys


def _notify_page(window, script):
    """回到界面线程再通知页面。键盘钩子里不能直接调网页。"""
    native = getattr(window, "native", None)
    if native is None:
        return

    def apply():
        try:
            window.evaluate_js(script)
        except Exception as exc:
            print("按键说话 %s" % exc, flush=True)

    from System import Action
    native.BeginInvoke(Action(apply))


def _notify_talk(window, held):
    _notify_page(window, "window.micHold&&window.micHold(%s)" % ("true" if held else "false"))


def _notify_capture(window, name, down):
    """登记快捷键时，把键名交给和直播间相同的记录函数。"""
    script = "window.micCaptureKey&&window.micCaptureKey(%s,%s)" % (
        json.dumps(name, ensure_ascii=True),
        "true" if down else "false",
    )
    _notify_page(window, script)


def _install_talk_hook(window, names):
    """桌宠即使不在前台，按住这组键也会开始录音。"""
    global _TALK_KEYS, _TALK_DOWN, _TALK_HOOK, _TALK_HOOK_PROC
    _TALK_KEYS = list(names)
    if _TALK_DOWN and not _talk_combo_down():
        _TALK_DOWN = False
        _notify_talk(window, False)
    if _TALK_HOOK:
        return
    user = _win32()
    prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

    def proc(code, wparam, lparam):
        try:
            _on_talk_key(window, code, int(wparam), lparam)
        except Exception as exc:
            print("按键钩子 %s" % exc, flush=True)
        return user.CallNextHookEx(None, code, wparam, lparam)

    _TALK_HOOK_PROC = prototype(proc)
    user.SetWindowsHookExW.argtypes = [ctypes.c_int, prototype, ctypes.c_void_p, ctypes.c_uint]
    user.SetWindowsHookExW.restype = ctypes.c_void_p
    user.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
    user.CallNextHookEx.restype = ctypes.c_ssize_t
    _TALK_HOOK = user.SetWindowsHookExW(13, _TALK_HOOK_PROC, None, 0)
    if not _TALK_HOOK:
        print("按键钩子没挂上", flush=True)


def _talk_combo_down():
    return bool(_TALK_KEYS) and all(_talk_key_down(name) for name in _TALK_KEYS)


def _on_talk_key(window, code, wparam, lparam):
    global _TALK_DOWN
    if code < 0 or wparam not in (0x0100, 0x0101, 0x0104, 0x0105):
        return
    if _TALK_CAPTURE:
        name = _name_of_vk(_vk_from_hook(lparam))
        down = wparam in (0x0100, 0x0104)
        if not name:
            return
        if down:
            if name in _TALK_SEEN:
                return
            _TALK_SEEN.add(name)
        else:
            _TALK_SEEN.discard(name)
        _notify_capture(window, name, down)
        return
    held = _talk_combo_down()
    if held and not _TALK_DOWN:
        _TALK_DOWN = True
        _notify_talk(window, True)
    elif not held and _TALK_DOWN:
        _TALK_DOWN = False
        _notify_talk(window, False)


class PetApi:
    """给页面调用的窗口操作。样式改动一律丢回界面线程，避免把窗口卡死。"""

    def __init__(self):
        # 窗口放在下划线属性上。pywebview 会递归扫描公开属性，扫到窗体就会卡死。
        self._window = None
        self.hwnd = 0
        self._pass = False

    def _ui(self, fn):
        native = getattr(self._window, "native", None)
        if native is None:
            return False
        from System import Action
        native.BeginInvoke(Action(fn))
        return True

    def resize_pet(self, scale):
        """按桌宠倍率放大窗口。右下角尽量不动，画布跟着变大，模型才不会被裁切。"""
        hwnd = self.hwnd or _hwnd_of(self._window)
        self.hwnd = hwnd
        if not hwnd:
            return False

        def apply():
            _resize_hwnd(hwnd, scale)

        self._ui(apply)
        return True

    def move_by(self, dx, dy):
        hwnd = self.hwnd or _hwnd_of(self._window)
        self.hwnd = hwnd
        if not hwnd:
            return False

        def apply():
            _move_hwnd(hwnd, dx, dy)

        self._ui(apply)
        return True

    def cursor(self):
        """返回光标在窗口里的 CSS 坐标。不在窗口上就返回空。"""
        if not self.hwnd and self._window is not None:
            self.hwnd = _hwnd_of(self._window)
        if not self.hwnd:
            return None
        spot = _cursor_css(self.hwnd)
        if spot is None:
            return None
        return {"x": spot[0], "y": spot[1]}

    def set_pass(self, enabled):
        """空白穿透。只改标记，真正的样式在界面线程上改。"""
        enabled = bool(enabled)
        if enabled == self._pass:
            return True
        self._pass = enabled
        hwnd = self.hwnd or _hwnd_of(self._window)
        self.hwnd = hwnd
        if not hwnd:
            return False

        def apply():
            _pass_style(hwnd, self._pass)

        self._ui(apply)
        return True

    def set_talk_key(self, keys):
        """设置按住说话的键。最多两个，空字符串表示取消。"""
        names = _normalize_talk_keys(keys)
        window = self._window

        def apply():
            _install_talk_hook(window, names)

        self._ui(apply)
        return True

    def set_talk_capture(self, enabled):
        """开始或结束登记快捷键。登记时不要求窗口在前台。"""
        if isinstance(enabled, str):
            flag = enabled.strip().lower() in ("1", "true", "yes", "on")
        else:
            flag = bool(enabled)
        window = self._window

        def apply():
            global _TALK_CAPTURE
            _TALK_CAPTURE = flag
            if not flag:
                _TALK_SEEN.clear()
            if window is not None:
                _install_talk_hook(window, _TALK_KEYS)

        self._ui(apply)
        return True

    def close_pet(self):
        if self._window is not None:
            self._window.destroy()
        return True


def _acquire_singleton():
    """已经有桌宠就直接退出，避免桌面上叠两个。"""
    global _PET_MUTEX
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, 0, PET_MUTEX_NAME)
    already = ctypes.get_last_error() == 183
    if already:
        if handle:
            kernel.CloseHandle(handle)
        print("已经有一个桌宠，这个进程退出", flush=True)
        return False
    if handle:
        _PET_MUTEX = handle
    return True


def _write_pid():
    PET_PID.write_text(str(os.getpid()), encoding="ascii")

    def clear():
        try:
            if PET_PID.read_text(encoding="ascii").strip() == str(os.getpid()):
                PET_PID.unlink()
        except OSError:
            pass

    atexit.register(clear)


def main():
    if not _acquire_singleton():
        return
    _write_pid()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    import webview

    _install_create_hook()
    _install_hook()
    api = PetApi()
    x, y = _work_origin()
    window = webview.create_window(
        TITLE,
        "http://%s:%d/?surface=pet" % (HOST, PORT),
        js_api=api,
        width=WIDTH,
        height=HEIGHT,
        x=x,
        y=y,
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
        shadow=False,
        focus=False,
        resizable=False,
        background_color="#000000",
        min_size=(312, 456),
    )
    api._window = window

    def before_show():
        api.hwnd = _hwnd_of(window)
        print("before_show hwnd %s style %s" % (api.hwnd, hex(_read_style(api.hwnd) if api.hwnd else 0)), flush=True)

    def after_load():
        print("loaded style %s" % hex(_read_style(api.hwnd) if api.hwnd else 0), flush=True)

    window.events.before_show += before_show
    window.events.loaded += after_load
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("桌宠窗口失败: %s" % exc, file=sys.stderr)
        raise
