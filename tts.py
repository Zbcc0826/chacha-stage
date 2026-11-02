# -*- coding: utf-8 -*-
"""中英日口播。默认走 Edge 在线语音，不把声音文件留在仓库里。"""

import asyncio
import threading
import time

# 同一句、同一嗓音短时间再点时直接复用，避免在线接口连续拒绝。
_cache = {}
_lock = threading.Lock()
_CACHE_TTL = 180


def _remember(key, data):
    """记下刚合成的 mp3，并丢掉过旧的条目。"""
    now = time.time()
    with _lock:
        _cache[key] = (now, data)
        if len(_cache) <= 40:
            return
        oldest = sorted(_cache, key=lambda item: _cache[item][0])[:-30]
        for item in oldest:
            _cache.pop(item, None)


def synthesize(text, voice, rate="+0%", pitch="+0Hz"):
    """合成 mp3 字节。rate、pitch 用 Edge 的写法，例如 +8%、+10Hz。失败会再试两次。"""
    spoken = (text or "").strip()
    if not spoken:
        return b""
    rate = rate or "+0%"
    pitch = pitch or "+0Hz"
    key = (spoken, voice, rate, pitch)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    import edge_tts

    async def _once():
        communicate = edge_tts.Communicate(spoken, voice, rate=rate, pitch=pitch)
        chunks = []
        async for piece in communicate.stream():
            if piece.get("type") == "audio":
                chunks.append(piece["data"])
        return b"".join(chunks)

    last_error = RuntimeError("语音是空的")
    for attempt in range(3):
        try:
            data = asyncio.run(_once())
            if data:
                _remember(key, data)
                return data
            last_error = RuntimeError("语音是空的")
        except Exception as exc:
            last_error = exc
        time.sleep(0.4 * (attempt + 1))
    raise last_error
