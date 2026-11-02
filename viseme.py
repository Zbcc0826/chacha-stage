# -*- coding: utf-8 -*-
"""按文本生成口型时间轴，不交给语言模型。

流程：
1. 把句子切成汉字、假名、英文和停顿。
2. 汉字用拼音的声母、韵母变成一串口型；假名按五十音；英文按字母组合。
3. 每个口型带权重。辅音短、元音长、标点是闭嘴。
4. 权重归一化到 0~1，播放端再对齐真实音频时长。

open 是 ParamMouthOpenY，0 闭、1 张大。
form 是 ParamMouthForm，正值扁（i），负值圆（u/o）。
"""

import re

# 名字到 (张嘴, 嘴形)。
SHAPES = {
    "AA": (0.92, 0.05),
    "EE": (0.30, 0.84),
    "EH": (0.52, 0.40),
    "OH": (0.72, -0.34),
    "OO": (0.40, -0.70),
    "BMP": (0.03, 0.0),
    "FV": (0.14, 0.12),
    "SS": (0.16, 0.52),
    "SH": (0.26, -0.12),
    "TH": (0.20, 0.22),
    "L": (0.34, 0.16),
    "D": (0.22, 0.08),
    "N": (0.10, 0.02),
    "NG": (0.16, 0.0),
    "G": (0.20, -0.05),
    "H": (0.28, 0.04),
    "W": (0.26, -0.42),
    "R": (0.30, -0.18),
    "REST": (0.0, 0.0),
}

# 韵母韵腹。鼻音韵尾单独再加一拍。
_FINAL = {
    "a": "AA", "ia": "AA", "ua": "AA",
    "o": "OH", "uo": "OH", "ou": "OH", "io": "OH",
    "e": "EH", "ie": "EH", "ue": "EH", "ve": "EH", "er": "EH",
    "i": "EE", "v": "EE",
    "u": "OO",
    "ai": "AA", "uai": "AA",
    "ei": "EH", "ui": "EH",
    "ao": "OH", "iao": "OH",
    "iu": "OO", "iou": "OO",
}

_INITIAL = {
    "b": "BMP", "p": "BMP", "m": "BMP",
    "f": "FV",
    "d": "D", "t": "D", "n": "N", "l": "L",
    "g": "G", "k": "G", "h": "H",
    "j": "SS", "q": "SS", "x": "SS",
    "z": "SS", "c": "SS", "s": "SS",
    "zh": "SH", "ch": "SH", "sh": "SH", "r": "SH",
    "w": "W",
}

_EN_CONS = {
    "b": "BMP", "p": "BMP", "m": "BMP",
    "f": "FV", "v": "FV",
    "s": "SS", "z": "SS", "x": "SS",
    "j": "SH",
    "l": "L", "r": "R",
    "d": "D", "t": "D", "n": "N",
    "g": "G", "k": "G", "q": "G",
    "h": "H", "w": "W",
}

_KANA_ROWS = {
    "あいうえお": "",
    "かきくけこ": "k",
    "がぎぐげご": "g",
    "さしすせそ": "s",
    "ざじずぜぞ": "z",
    "たちつてと": "t",
    "だぢづでど": "d",
    "なにぬねの": "n",
    "はひふへほ": "h",
    "ばびぶべぼ": "b",
    "ぱぴぷぺぽ": "p",
    "まみむめも": "m",
    "やゆよ": "y",
    "らりるれろ": "r",
    "わをん": "w",
}
_KANA_CONS = {
    "": None, "k": "G", "g": "G", "s": "SS", "z": "SS",
    "t": "D", "d": "D", "n": "N", "h": "H", "b": "BMP",
    "p": "BMP", "m": "BMP", "y": None, "r": "R", "w": "W", "f": "FV",
}
_YOUON = {"ゃ": "a", "ゅ": "u", "ょ": "o", "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
_VOWEL_SHAPE = {"a": "AA", "i": "EE", "u": "OO", "e": "EH", "o": "OH"}

_SEGMENTS = re.compile(
    r"[\u4e00-\u9fff]+|[\u3040-\u30ffー]+|[A-Za-z']+|\d+|[，。！？、；：…,.!?;:\n\r]+"
)


def _kana_table():
    """五十音到 (辅音, 元音)。片假名一并登记。"""
    table = {}
    vowels = "aiueo"
    for row, cons in _KANA_ROWS.items():
        if row == "やゆよ":
            pairs = zip(row, "auo")
        elif row == "わをん":
            pairs = (("わ", "a"), ("を", "o"), ("ん", "n"))
        else:
            pairs = zip(row, vowels)
        for kana, vowel in pairs:
            table[kana] = (cons, vowel)
            kata = chr(ord(kana) + 0x60)
            table[kata] = table[kana]
    table["ふ"] = ("f", "u")
    table["フ"] = ("f", "u")
    table["を"] = ("", "o")
    table["ヲ"] = ("", "o")
    return table


_KANA = _kana_table()


def _append(phones, weight, shape):
    if shape:
        phones.append((weight, shape))


def chinese_syllables(text):
    """一个汉字一个音节：声母短、韵腹长，n/ng 再收一下。"""
    from pypinyin import Style, pinyin

    initials = pinyin(text, style=Style.INITIALS, strict=False)
    finals = pinyin(text, style=Style.FINALS, strict=False)
    phones = []
    for ini, fin in zip(initials, finals):
        initial = (ini[0] or "").lower()
        final = (fin[0] or "").lower().replace("ü", "v")
        if not initial and not final:
            continue
        _append(phones, 0.28 if initial == "w" else 0.38, _INITIAL.get(initial))
        coda = None
        base = final
        if base.endswith("ng"):
            coda = "NG"
            base = base[:-2]
        elif base.endswith("n"):
            coda = "N"
            base = base[:-1]
        _append(phones, 1.0, _FINAL.get(base) or _FINAL.get(final) or "EH")
        _append(phones, 0.32, coda)
    return phones


def kana_phones(text):
    """假名按拍拆。促音闭嘴，拨音收鼻，小ゃゅょ并进前一个音节。"""
    phones = []
    chars = list(text)
    index = 0
    while index < len(chars):
        char = chars[index]
        if char in "っッ":
            _append(phones, 0.42, "BMP")
            index += 1
            continue
        if char in "ー":
            previous = phones[-1][1] if phones else "AA"
            if previous in ("BMP", "N", "REST"):
                previous = "AA"
            _append(phones, 0.7, previous)
            index += 1
            continue
        info = _KANA.get(char)
        if not info:
            index += 1
            continue
        cons, vowel = info
        if vowel == "n":
            _append(phones, 0.55, "N")
            index += 1
            continue
        if index + 1 < len(chars):
            small = chars[index + 1]
            small_hira = chr(ord(small) - 0x60) if "\u30a0" <= small <= "\u30ff" else small
            if small_hira in _YOUON:
                vowel = _YOUON[small_hira]
                index += 1
        _append(phones, 0.36, _KANA_CONS.get(cons))
        _append(phones, 1.0, _VOWEL_SHAPE[vowel])
        index += 1
    return phones


def english_phones(text):
    """英文按字母组合，不查词典。双写辅音合成一拍。"""
    source = text.lower()
    phones = []
    index = 0
    size = len(source)
    while index < size:
        if source[index].isspace() or source[index] == "'":
            if source[index].isspace():
                _append(phones, 0.3, "REST")
            index += 1
            continue
        pair = source[index:index + 2]
        if pair in ("ch", "sh"):
            _append(phones, 0.45, "SH")
            index += 2
            continue
        if pair == "th":
            _append(phones, 0.4, "TH")
            index += 2
            continue
        if pair == "ph":
            _append(phones, 0.4, "FV")
            index += 2
            continue
        if pair == "ng":
            _append(phones, 0.42, "NG")
            index += 2
            continue
        if pair in ("ee", "ea"):
            _append(phones, 1.0, "EE")
            index += 2
            continue
        if pair in ("oo",):
            _append(phones, 1.0, "OO")
            index += 2
            continue
        if pair in ("ai", "ay", "ei"):
            _append(phones, 1.0, "EH")
            index += 2
            continue
        if pair in ("oa", "ow", "oi", "oy", "au", "aw", "ou"):
            _append(phones, 1.0, "OH")
            index += 2
            continue
        char = source[index]
        nxt = source[index + 1] if index + 1 < size else ""
        if char == "c":
            _append(phones, 0.4, "SS" if nxt in "eiy" else "G")
            index += 1
            continue
        if char in "aeiou":
            _append(phones, 1.0, {"a": "AA", "e": "EH", "i": "EE", "o": "OH", "u": "OO"}[char])
            index += 1
            continue
        if char == "y":
            _append(phones, 0.8, "EE")
            index += 1
            continue
        shape = _EN_CONS.get(char)
        if shape:
            while index + 1 < size and source[index + 1] == char:
                index += 1
            _append(phones, 0.42, shape)
            index += 1
            continue
        index += 1
    return phones


def _segments(text, lang):
    """按文字种类切段。日语里的汉字没有读音表，只能给一个中性开口。"""
    phones = []
    cursor = 0
    for match in _SEGMENTS.finditer(text):
        if match.start() > cursor:
            phones.append((0.25, "REST"))
        chunk = match.group(0)
        cursor = match.end()
        head = chunk[0]
        if "\u4e00" <= head <= "\u9fff":
            if lang == "ja":
                phones.extend((0.9, "AA") for _ in chunk)
            else:
                phones.extend(chinese_syllables(chunk))
        elif "\u3040" <= head <= "\u30ff" or head == "ー":
            phones.extend(kana_phones(chunk))
        elif head.isalpha() or head == "'":
            phones.extend(english_phones(chunk))
        elif head.isdigit():
            phones.extend((0.7, "EH") for _ in chunk if _ != " ")
        else:
            phones.append((0.6, "REST"))
    return phones


def _track(phones):
    """把权重铺到 0~1。句尾留一点闭嘴，避免最后一个音挂着。"""
    if not phones:
        return [
            {"t": 0, "open": 0, "form": 0},
            {"t": 1, "open": 0, "form": 0},
        ]
    total = sum(weight for weight, _shape in phones) or 1
    usable = 0.94
    points = []
    cursor = 0.0
    for weight, shape in phones:
        start = cursor / total * usable
        cursor += weight
        end = cursor / total * usable
        opened, formed = SHAPES[shape]
        points.append((start, opened, formed))
        if end - start > 0.012:
            points.append((end - 0.004, opened, formed))
    points.append((1.0, 0.0, 0.0))
    track = []
    for unit, opened, formed in points:
        unit = round(min(1.0, max(0.0, unit)), 3)
        opened = round(opened, 3)
        formed = round(formed, 3)
        if track and track[-1]["t"] == unit:
            track[-1] = {"t": unit, "open": opened, "form": formed}
        else:
            track.append({"t": unit, "open": opened, "form": formed})
    if track[0]["t"] > 0:
        track.insert(0, {"t": 0, "open": 0, "form": 0})
    return track


def mouth_track(text, lang="zh"):
    """生成整句口型。lang 只影响汉字：中文走拼音，日文没有读音表时用中性开口。"""
    spoken = text or ""
    return _track(_segments(spoken, lang if lang in ("zh", "ja", "en") else "zh"))
