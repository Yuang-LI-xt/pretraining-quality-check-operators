#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
basic functions for mojibake_detect (V6 enhanced version)
"""

import unicodedata


def is_fffd(char):
    """check if the char is U+FFFD (replacement character)"""
    return char == '\ufffd'


def is_cscn(char):
    """check if the char is Cs (surrogate) or Cn (unassigned)"""
    char_cat = unicodedata.category(char)
    return char_cat in ("Cs", "Cn")


def is_c1_control(char):
    """check if the char is C1 control character (0x80-0x9F)"""
    cp = ord(char)
    return 0x0080 <= cp <= 0x009F


def is_pua(char):
    """check if the char is in Private Use Area (PUA)"""
    cp = ord(char)
    return 0xE000 <= cp <= 0xF8FF


KNOWN_PUA_NOISE = {
    '\uf04a',  # font-private smiley/noise observed in long_dialog samples
    '\uf8ff',  # Apple logo private-use glyph
}


def is_known_pua_noise(char):
    """check if the PUA char is an explicitly known removable noise char"""
    return char in KNOWN_PUA_NOISE


def is_variation_selector(char):
    """check if the char is a Unicode variation selector"""
    cp = ord(char)
    return (0xFE00 <= cp <= 0xFE0F) or (0xE0100 <= cp <= 0xE01EF)


def is_styled_math_alnum(char):
    """check if the char is a mathematical styled alphanumeric symbol"""
    cp = ord(char)
    return 0x1D400 <= cp <= 0x1D7FF


# ---------------------------------------------------------------------------
# Exotic combining marks: 罕见块/孤儿字符，正常文本中几乎不出现 → 零容忍
# 与 combining_decoration 正交：不看连续 run，只看字符身份
# ---------------------------------------------------------------------------
EXOTIC_COMBINING_RANGES = (
    (0x1AB0, 0x1AFF),   # Combining Diacritical Marks Extended（扩展，罕见）
    (0x1DC0, 0x1DFF),   # Combining Diacritical Marks Supplement（补充，极罕见，中古文献）
    (0xFE20, 0xFE2F),   # Combining Half Marks（古抄本拼接用）
)

# 主块 U+0300-036F 中的孤儿罕见字符（其余主块字符是合法重音，不能动）
EXOTIC_COMBINING_SINGLES = {
    '͈',  # COMBINING DOUBLE VERTICAL LINE BELOW
    '͒',  # COMBINING FERMATA（音乐符号）
}


def is_exotic_combining(char):
    """check if the char is an exotic combining mark (zero-tolerance)"""
    cp = ord(char)
    for lo, hi in EXOTIC_COMBINING_RANGES:
        if lo <= cp <= hi:
            return True
    return char in EXOTIC_COMBINING_SINGLES


# abnormal space chars: maps char → replacement (' ' = space, None = delete)
ABNORMAL_SPACES = {
    ' ': ' ',   # NO-BREAK SPACE        → space
    ' ': ' ',   # EM SPACE              → space
    '　': ' ',   # IDEOGRAPHIC SPACE     → space
    ' ': None,  # NARROW NO-BREAK SPACE → delete
    ' ': None,  # THIN SPACE            → delete
}


def is_abnormal_space(char):
    """check if the char is an abnormal space character"""
    return char in ABNORMAL_SPACES


BIDI_CONTROL_CHARS = {
    '\u202a',  # LEFT-TO-RIGHT EMBEDDING
    '\u202b',  # RIGHT-TO-LEFT EMBEDDING
    '\u202c',  # POP DIRECTIONAL FORMATTING
    '\u202d',  # LEFT-TO-RIGHT OVERRIDE
    '\u202e',  # RIGHT-TO-LEFT OVERRIDE
    '\u2066',  # LEFT-TO-RIGHT ISOLATE
    '\u2067',  # RIGHT-TO-LEFT ISOLATE
    '\u2068',  # FIRST STRONG ISOLATE
    '\u2069',  # POP DIRECTIONAL ISOLATE
    '\u200e',  # LEFT-TO-RIGHT MARK
    '\u200f',  # RIGHT-TO-LEFT MARK
}


def is_bidi_control(char):
    """check if the char is a bidi control mark removed by V5"""
    return char in BIDI_CONTROL_CHARS


DECORATIVE_SYMBOL_RANGES = (
    (0x2500, 0x257F),  # Box Drawing
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2600, 0x26FF),  # Miscellaneous Symbols
    (0x2700, 0x27BF),  # Dingbats
)

SEMANTIC_FORM_SYMBOL_CHARS = {
    '\u25a0',  # BLACK SQUARE
    '\u25a1',  # WHITE SQUARE
    '\u25cb',  # WHITE CIRCLE
    '\u25cf',  # BLACK CIRCLE
    '\u25ef',  # LARGE CIRCLE
    '\u2610',  # BALLOT BOX
    '\u2611',  # BALLOT BOX WITH CHECK
    '\u2612',  # BALLOT BOX WITH X
    '\u2713',  # CHECK MARK
    '\u2714',  # HEAVY CHECK MARK
    '\u2717',  # BALLOT X
    '\u2718',  # HEAVY BALLOT X
}


def is_decorative_symbol(char):
    """check if the char is in the V6 decorative symbol ranges"""
    if char in SEMANTIC_FORM_SYMBOL_CHARS:
        return False
    cp = ord(char)
    for lo, hi in DECORATIVE_SYMBOL_RANGES:
        if lo <= cp <= hi:
            return True
    return False


DECORATIVE_COMBINING_CHARS = {
    '\u0336',
    '\u033f',
    '\u035c',
    '\u035d',
    '\u035e',
    '\u035f',
    '\u0360',
    '\u0361',
}


def is_decorative_combining(char):
    """check if the char is an exact V6 decorative combining whitelist hit"""
    return char in DECORATIVE_COMBINING_CHARS


def is_combining_mark(char):
    """check if the char is a Unicode combining mark (excluding variation selectors)"""
    cp = ord(char)
    # variation selectors are handled by their own rule
    if (0xFE00 <= cp <= 0xFE0F) or (0xE0100 <= cp <= 0xE01EF):
        return False
    return unicodedata.category(char) in ("Mn", "Mc", "Me")


def is_math_flood_symbol(char):
    """check if the char is a symbol commonly used in flood/separator runs"""
    cat = unicodedata.category(char)
    return cat in ("Sm", "So") or char in "=+~×═"


def is_control_format_char(char):
    """check if the char is a format/line separator control artifact"""
    return unicodedata.category(char) in ("Cf", "Zl", "Zp")


# 绝对垃圾的不可见字符（任何自然文本正文中都不该出现）
INVIS_ALWAYS_GARBAGE = {
    '\uFEFF',  # BOM，只在文件首字节合法，出现在正文中=乱码
    '\u2060',  # WORD JOINER，排版残留
    '\u2061',  # FUNCTION APPLICATION，仅 MathML 用
    '\u034F',  # COMBINING GRAPHEME JOINER，极罕见
    '\u00AD',  # SOFT HYPHEN，网页爬取残留
}

# 行间标注字符 U+FFF9-U+FFFB（自然文本中永远不该出现）
INTERLINEAR_CHARS = set(chr(c) for c in range(0xFFF9, 0xFFFC))

# Specials 区段垃圾 U+FFF0-U+FFFC（对象替换符、保留码位）
SPECIALS_GARBAGE = set(chr(c) for c in range(0xFFF0, 0xFFFD))

# 条件性零宽字符（V6.0 不做整条判废；命中后由上层字符级清洗）
CONDITIONAL_ZW = {
    '\u200B',  # 零宽空格 — 泰/高棉/缅甸/老挝分词用
    '\u200C',  # ZWNJ — 波斯/阿拉伯/印度文字连接控制
    '\u200D',  # ZWJ — 印度文合字、emoji 组合序列
    '\u200E',  # LTR 标记 — 双向文本
    '\u200F',  # RTL 标记 — 双向文本
}

# 所有零宽/不可见字符合集（用于洪泛检测）
ALL_ZW_CHARS = INVIS_ALWAYS_GARBAGE | CONDITIONAL_ZW | INTERLINEAR_CHARS

# ---------------------------------------------------------------------------
# 历史辅助函数：V6.0 检测链路不使用小语种豁免/整条判废。
# 保留这些范围只是为了兼容外部可能直接 import 的旧代码。
# ---------------------------------------------------------------------------
LEGIT_ZW_SCRIPT_RANGES = (
    (0x0600, 0x06FF),   # 阿拉伯/波斯/乌尔都
    (0xFB50, 0xFDFF),   # 阿拉伯表现形式 A
    (0xFE70, 0xFEFF),   # 阿拉伯表现形式 B
    (0x0900, 0x097F),   # 天城文(印地语)
    (0x0980, 0x09FF),   # 孟加拉文
    (0x0A00, 0x0A7F),   # 古木基文(旁遮普)
    (0x0A80, 0x0AFF),   # 古吉拉特文
    (0x0B00, 0x0B7F),   # 奥里亚文
    (0x0B80, 0x0BFF),   # 泰米尔文
    (0x0C00, 0x0C7F),   # 泰卢固文
    (0x0C80, 0x0CFF),   # 卡纳达文
    (0x0D00, 0x0D7F),   # 马拉雅拉姆文
    (0x0D80, 0x0DFF),   # 僧伽罗文
    (0x1000, 0x109F),   # 缅甸文
    (0x1780, 0x17FF),   # 高棉文
    (0x0E00, 0x0E7F),   # 泰文
    (0x0E80, 0x0EFF),   # 老挝文
    (0x1200, 0x137F),   # 埃塞俄比亚文
    (0x0590, 0x05FF),   # 希伯来文
    (0xFB1D, 0xFB4F),   # 希伯来表现形式
)


def legit_script_ratio(text):
    """计算文本中合法使用零宽字符的文字系统占比"""
    total = 0
    hits = 0
    for c in text:
        if c.isspace():
            continue
        total += 1
        cp = ord(c)
        for lo, hi in LEGIT_ZW_SCRIPT_RANGES:
            if lo <= cp <= hi:
                hits += 1
                break
    return hits / total if total else 0.0


# ---------------------------------------------------------------------------
# 历史辅助函数：V6.0 不使用 complex script 来触发 combining_flood。
# ---------------------------------------------------------------------------
COMPLEX_SCRIPT_RANGES = (
    (0x0590, 0x05FF),   # 希伯来文
    (0x0600, 0x06FF),   # 阿拉伯/波斯/乌尔都
    (0x0700, 0x074F),   # 叙利亚文
    (0x0780, 0x07BF),   # 塔安那文
    (0x0900, 0x097F),   # 天城文(印地语)
    (0x0980, 0x09FF),   # 孟加拉文
    (0x0A00, 0x0A7F),   # 古木基文(旁遮普)
    (0x0A80, 0x0AFF),   # 古吉拉特文
    (0x0B00, 0x0B7F),   # 奥里亚文
    (0x0B80, 0x0BFF),   # 泰米尔文
    (0x0C00, 0x0C7F),   # 泰卢固文
    (0x0C80, 0x0CFF),   # 卡纳达文
    (0x0D00, 0x0D7F),   # 马拉雅拉姆文
    (0x0D80, 0x0DFF),   # 僧伽罗文
    (0x0E00, 0x0E7F),   # 泰文
    (0x0E80, 0x0EFF),   # 老挝文
    (0x0F00, 0x0FFF),   # 藏文
    (0x1000, 0x109F),   # 缅甸文
    (0x1200, 0x137F),   # 埃塞俄比亚文
    (0x1780, 0x17FF),   # 高棉文
    (0xFB1D, 0xFB4F),   # 希伯来表现形式
    (0xFB50, 0xFDFF),   # 阿拉伯表现形式 A
    (0xFE70, 0xFEFF),   # 阿拉伯表现形式 B
)


def has_complex_script(text):
    """Historical helper; V6.0 does not use this for record-level decisions."""
    for ch in text:
        cp = ord(ch)
        for lo, hi in COMPLEX_SCRIPT_RANGES:
            if lo <= cp <= hi:
                return True
    return False
