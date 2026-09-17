#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean Unicode decoration characters from JSONL or XLSX files.

Rules (V6.0):
  - variation_selector:    U+FE00-U+FE0F, U+E0100-U+E01EF → delete selector only
  - styled_math_alnum:     U+1D400-U+1D7FF                 → NFKC normalize
  - abnormal_space:        U+00A0, U+2003, U+3000          → replace with U+0020
                           U+202F, U+2009                  → delete
  - bidi_control:          U+202A-U+202E, U+2066-U+2069, U+200E/U+200F → delete
  - pua_contextual:        high-confidence PUA repairs/deletions only:
                           U+F0B7 at list-item position → "- "
                           U+F07E between numeric range endpoints → "~"
                           U+E010 after number/letter → "．"
                           selected ebook PUA punctuation/footnote markers
                           → repair/delete
  - pua_known_noise:       known PUA noise U+F8FF Apple logo / U+F04A
                           font-private smiley → delete
  - decorative_symbol:     U+2500-U+27BF box/block/geometric/misc/dingbat
                           decorative symbols → delete, except semantic form
                           symbols such as □/☐/☑/☒/✓/✗
  - decorative_combining:  exact whitelist U+0336, U+033F, U+035C-U+0361
                           → delete. Does not delete Unicode Mn/Me by category.
  - control_invisible:     U+FEFF, U+200B-U+200F, U+2060-U+2061,
                           U+00AD, U+034F                  → delete
  - line_separator:        U+2028 → U+000A, U+2029 → U+000A U+000A
  - html_entity:           named/decimal/hex HTML entities → delete whole entity string
  - forbidden_strings:     HTML residues / training tokens / literal escape bugs
                           → delete matched spans
  - repeated_noise_token:  same suspicious alphanumeric token repeated ≥5 times
                           → delete the whole repeated run
  - hex_blob:              long hexadecimal/binary dump blocks → delete
                           high-confidence spans; common hashes are preserved
  - math_symbol_flood:     same math/symbol/separator char repeated ≥4 times
                           → delete the whole run
  - emoji_all:             all emoji + emoji attachments (ZWJ, keycap U+20E3,
                           enclosing U+20E4, etc.) → delete unconditionally.
                           Keycap emoji and emoji variation sequences are deleted
                           as whole visible units when the base is emoji-like.

注意：不会删除 Excel 标注用的全角方括号【】本身。
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait


VARIATION_SELECTOR_RANGES = (
    (0xFE00, 0xFE0F),
    (0xE0100, 0xE01EF),
)

STYLED_MATH_ALNUM_RANGES = (
    (0x1D400, 0x1D7FF),
)

# Exotic combining marks: 罕见块/孤儿字符，正常文本几乎不出现 → 零容忍
EXOTIC_COMBINING_RANGES = (
    (0x1AB0, 0x1AFF),   # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),   # Combining Diacritical Marks Supplement（中古文献）
    (0xFE20, 0xFE2F),   # Combining Half Marks（古抄本）
)
EXOTIC_COMBINING_SINGLES = {
    '͈',  # COMBINING DOUBLE VERTICAL LINE BELOW
    '͒',  # COMBINING FERMATA
}

# Emoji 与所有 emoji 附属字符 — 全部零容忍删除
# V6 默认先匹配 decorative_symbol，所以 U+2600-U+27BF 会优先按装饰符号计数；
# 若用户只启用 emoji_all，该范围仍会被 emoji_all 覆盖。
EMOJI_RANGES = (
    (0x2600, 0x27BF),   # Misc Symbols + Dingbats（★❤✓✨ 等）
    (0x2B00, 0x2BFF),   # Misc Symbols and Arrows（⭐⬆ 等）
    (0x1F000, 0x1FFFF), # SMP emoji 全块（emoticons / 旗帜 / 食物 / 动物 / 表情）
)
EMOJI_ATTACHMENT_RANGES = (
    (0x20D0, 0x20FF),   # Combining Marks for Symbols（keycap U+20E3 / enclosing U+20E4 等）
)
EMOJI_ATTACHMENT_CHARS = {
    '‍',  # U+200D ZWJ — emoji 序列连接符（🏳‍🌈 / 👨‍👩‍👧 中间的连接器）
}

# Bases that become emoji when followed by VS16. This covers common emoji
# variation-sequence bases such as ▪️, ™️, ©️, and arrows, without treating
# arbitrary CJK ideographic variation sequences as emoji.
EMOJI_VARIATION_BASE_RANGES = (
    (0x203C, 0x2049),
    (0x2122, 0x2139),
    (0x2194, 0x21AA),
    (0x231A, 0x231B),
    (0x23CF, 0x23FA),
    (0x24C2, 0x24C2),
    (0x25AA, 0x25AB),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x25FB, 0x25FE),
    (0x2600, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3299),
)
EMOJI_VARIATION_BASE_CHARS = set("#*0123456789©®")
KEYCAP_BASE_CHARS = set("#*0123456789")

# Invisible/control-like artifacts to delete unconditionally. U+2028/U+2029
# are handled by line_separator because they mean line/paragraph breaks.
CONTROL_INVISIBLE_CHARS = {
    '\ufeff',  # BOM / ZERO WIDTH NO-BREAK SPACE in body text
    '\u200b',  # ZERO WIDTH SPACE
    '\u200c',  # ZERO WIDTH NON-JOINER
    '\u200d',  # ZERO WIDTH JOINER
    '\u200e',  # LEFT-TO-RIGHT MARK
    '\u200f',  # RIGHT-TO-LEFT MARK
    '\u2060',  # WORD JOINER
    '\u2061',  # FUNCTION APPLICATION
    '\u034f',  # COMBINING GRAPHEME JOINER
    '\u00ad',  # SOFT HYPHEN
}

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

LINE_SEPARATOR_MAP = {
    '\u2028': '\n',    # LINE SEPARATOR
    '\u2029': '\n\n',  # PARAGRAPH SEPARATOR
}

PUA_CONTEXTUAL_CHARS = {
    '\uf0b7',
    '\uf07e',
    '\ue010',
    '\ue011',
    '\ue10b',
    '\ue11a',
    '\ue11b',
    '\ue11c',
    '\ue1bd',
    '\ue618',
}

PUA_KNOWN_NOISE_CHARS = {
    '\uf8ff',  # Apple logo private-use glyph
    '\uf04a',  # font-private smiley/noise glyph
}

DECORATIVE_SYMBOL_RANGES = (
    (0x2500, 0x257F),  # Box Drawing
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2600, 0x26FF),  # Miscellaneous Symbols
    (0x2700, 0x27BF),  # Dingbats
)

SEMANTIC_FORM_SYMBOL_CHARS = {
    '\u25a0',  # BLACK SQUARE: selected checkbox/list marker
    '\u25a1',  # WHITE SQUARE: empty checkbox
    '\u25cb',  # WHITE CIRCLE: radio option
    '\u25cf',  # BLACK CIRCLE: selected radio/list marker
    '\u25ef',  # LARGE CIRCLE: radio option
    '\u2610',  # BALLOT BOX
    '\u2611',  # BALLOT BOX WITH CHECK
    '\u2612',  # BALLOT BOX WITH X
    '\u2713',  # CHECK MARK
    '\u2714',  # HEAVY CHECK MARK
    '\u2717',  # BALLOT X
    '\u2718',  # HEAVY BALLOT X
}

DECORATIVE_COMBINING_CHARS = {
    '\u0336',  # COMBINING LONG STROKE OVERLAY
    '\u033f',  # COMBINING DOUBLE OVERLINE
    '\u035c',  # COMBINING DOUBLE BREVE BELOW
    '\u035d',  # COMBINING DOUBLE BREVE
    '\u035e',  # COMBINING DOUBLE MACRON
    '\u035f',  # COMBINING DOUBLE MACRON BELOW
    '\u0360',  # COMBINING DOUBLE TILDE
    '\u0361',  # COMBINING DOUBLE INVERTED BREVE
}

# abnormal spaces: map char → replacement (' ' = replace with space, None = delete)
ABNORMAL_SPACE_MAP = {
    ' ': ' ',   # NO-BREAK SPACE        → space
    ' ': ' ',   # EM SPACE              → space
    '　': ' ',   # IDEOGRAPHIC SPACE     → space
    ' ': None,  # NARROW NO-BREAK SPACE → delete
    ' ': None,  # THIN SPACE            → delete
}

# combining_decoration: 一个 base 字符后面连续挂 ≥N 个 combining mark
# 才判定为装饰，删 combining 保 base（保护 résumé / x̄ 这种合法用法）
COMBINING_DECORATION_RUN_THRESHOLD = 2

DEFAULT_RULES = (
    "variation_selector",
    "styled_math_alnum",
    "abnormal_space",
    "bidi_control",
    "pua_known_noise",
    "pua_contextual",
    "decorative_symbol",
    "decorative_combining",
    "emoji_all",
    "control_invisible",
    "line_separator",
    "html_entity",
    "forbidden_strings",
    "repeated_noise_token",
    "hex_blob",
    "math_symbol_flood",
    "line_boundary_space",
    "blank_line_collapse",
)

OPTIONAL_LEGACY_RULES = (
    "combining_decoration",
    "exotic_combining",
)
ALL_RULES = DEFAULT_RULES + OPTIONAL_LEGACY_RULES
RULE_HELP = ",".join(ALL_RULES)

CLEANING_META_VERSION = "v6.0"
CLEANING_META_KEY = "cleaning_v6"
FALLBACK_CLEANING_META_KEY = "_cleaning_v6"
DEFAULT_MAX_META_CHANGES = 500
DEFAULT_META_DETAIL = "spans"
DEFAULT_MAX_META_SPANS = 0
META_TEXT_PREVIEW_CHARS = 120

NUMERIC_RANGE_LEFT_RE = re.compile(r"[\d%‰℃°]\s*$")
NUMERIC_RANGE_RIGHT_RE = re.compile(r"^\s*\d")
PUA_DOT_LEFT_RE = re.compile(r"[0-9A-Za-z\)\]）】》”’]$")
PUA_DOT_RIGHT_RE = re.compile(r"^\s*[\w\u4e00-\u9fff“”\"']")
PUA_HYPHEN_LEFT_RE = re.compile(r"[A-Za-z]$")
PUA_HYPHEN_RIGHT_RE = re.compile(r"^[A-Za-z]")

FORBIDDEN_STRINGS = (
    "<br>", "<br/>", "<br />", "</br>",
    "&lt;br&gt;", "&lt;/br&gt;",
    "<|im_start|>", "<|im_end|>", "<|endoftext|>",
    "<|fim_prefix|>", "<|fim_middle|>", "<|fim_suffix|>",
    "<arg_key>", "</arg_key>", "<arg_value>", "</arg_value>",
    "<is_displaying_contents>", "</is_displaying_contents>",
    "\x03",
    "\x04",
)
HTML_ENTITY_PATTERN = re.compile(r'&[a-zA-Z]{2,10};|&#[0-9]{1,7};|&#[xX][0-9a-fA-F]{1,6};')
LITERAL_ESCAPE_PATTERN = re.compile(r'\\{1,2}[ntr](?![A-Za-z{])')

MATH_SYMBOL_FLOOD_THRESHOLD = 4
MATH_SYMBOL_FLOOD_EXTRA_CHARS = set("=+~×═")

REPEATED_NOISE_TOKEN_MIN_REPEATS = 5
REPEATED_NOISE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]{5,128}(?![A-Za-z0-9])"
)
REPEATED_NOISE_SEPARATOR_PATTERN = re.compile(r"^[\s,;，、|]+$")
REPEATED_NOISE_INTERNAL_RUN_PATTERN = re.compile(r"([A-Za-z0-9])\1{3,}")

HEX_BLOB_MIN_CONTIGUOUS = 64
HEX_BLOB_MIN_WRAPPED_HEX = 64
HEX_BLOB_MIN_LINE_HEX = 64
HEX_BLOB_MIN_RATIO = 0.95
HEX_CONTIGUOUS_PATTERN = re.compile(r"(?<![A-Za-z0-9])[0-9A-Fa-f]+(?![A-Za-z0-9])")
HEX_WRAPPED_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[0-9A-Fa-f](?:[0-9A-Fa-f]|[ \t\r\n]){63,}[0-9A-Fa-f](?![A-Za-z0-9])"
)


def _in_ranges(cp, ranges):
    for start, end in ranges:
        if start <= cp <= end:
            return True
    return False


def classify_char(ch):
    """Return (rule, replacement) or (None, None).

    replacement is the output character ('' for delete, ' ' for space substitution).
    """
    cp = ord(ch)
    if _in_ranges(cp, VARIATION_SELECTOR_RANGES):
        return "variation_selector", ""
    if ch in BIDI_CONTROL_CHARS:
        return "bidi_control", ""
    if ch in DECORATIVE_COMBINING_CHARS:
        return "decorative_combining", ""
    if ch in PUA_KNOWN_NOISE_CHARS:
        return "pua_known_noise", ""
    if ch in SEMANTIC_FORM_SYMBOL_CHARS:
        return None, None
    if _in_ranges(cp, DECORATIVE_SYMBOL_RANGES):
        return "decorative_symbol", ""
    if (_in_ranges(cp, EMOJI_RANGES)
            or _in_ranges(cp, EMOJI_ATTACHMENT_RANGES)
            or ch in EMOJI_ATTACHMENT_CHARS):
        return "emoji_all", ""
    if ch in CONTROL_INVISIBLE_CHARS:
        return "control_invisible", ""
    if ch in LINE_SEPARATOR_MAP:
        return "line_separator", LINE_SEPARATOR_MAP[ch]
    if _in_ranges(cp, STYLED_MATH_ALNUM_RANGES):
        normalized = unicodedata.normalize("NFKC", ch)
        return "styled_math_alnum", normalized if normalized != ch else ""
    if _in_ranges(cp, EXOTIC_COMBINING_RANGES) or ch in EXOTIC_COMBINING_SINGLES:
        return "exotic_combining", ""
    if ch in ABNORMAL_SPACE_MAP:
        repl = ABNORMAL_SPACE_MAP[ch]
        return "abnormal_space", (' ' if repl is not None else "")
    return None, None


def is_emoji_variation_base(ch):
    """Return True if ch is a common base for emoji variation sequences."""
    cp = ord(ch)
    return ch in EMOJI_VARIATION_BASE_CHARS or _in_ranges(cp, EMOJI_VARIATION_BASE_RANGES)


def _previous_non_replaced_base(chars, idx, replacements):
    """Find previous base index, skipping an already-marked variation selector."""
    prev = idx - 1
    if prev >= 0 and chars[prev] in ('\ufe0e', '\ufe0f'):
        prev -= 1
    return prev if prev >= 0 else None


def char_info(ch, rule, replacement):
    return {
        "char": ch,
        "unicode_escape": ch.encode("unicode_escape").decode("ascii"),
        "codepoint": "U+{:04X}".format(ord(ch)),
        "name": unicodedata.name(ch, "<unnamed>"),
        "category": unicodedata.category(ch),
        "rule": rule,
        "replacement": repr(replacement),
    }


def _is_combining_mark(ch):
    """check if char is Mn/Mc/Me but NOT a variation selector"""
    cp = ord(ch)
    if (0xFE00 <= cp <= 0xFE0F) or (0xE0100 <= cp <= 0xE01EF):
        return False
    return unicodedata.category(ch) in ("Mn", "Mc", "Me")


def _scan_combining_decoration(chars, threshold):
    """Find combining-mark runs ≥ threshold attached to a base char.

    Returns set of indices to delete (only the combining marks, base is kept).
    Leading combining marks (no base) are also marked for deletion.
    """
    n = len(chars)
    to_delete = set()
    i = 0
    while i < n:
        if _is_combining_mark(chars[i]):
            # 开头/前面没 base 的 combining mark：异常，直接删
            to_delete.add(i)
            i += 1
            continue
        # base 字符，扫后面连续的 combining
        j = i + 1
        while j < n and _is_combining_mark(chars[j]):
            j += 1
        run = j - (i + 1)
        if run >= threshold:
            for k in range(i + 1, j):
                to_delete.add(k)
        i = j if j > i else i + 1
    return to_delete


def _is_line_start_after_indent(chars, idx):
    """Return True when idx is at text/line start after optional horizontal spaces."""
    j = idx - 1
    while j >= 0 and chars[j] in (" ", "\t", "\u00a0", "\u3000"):
        j -= 1
    return j < 0 or chars[j] in ("\n", "\r", "\u2028", "\u2029")


def _next_nonspace_char(chars, idx):
    j = idx + 1
    while j < len(chars) and chars[j] in (" ", "\t", "\u00a0", "\u3000"):
        j += 1
    return chars[j] if j < len(chars) else ""


def _prev_window(chars, idx, size=12):
    return "".join(chars[max(0, idx - size):idx])


def _next_window(chars, idx, size=12):
    return "".join(chars[idx + 1:min(len(chars), idx + 1 + size)])


def _scan_pua_contextual(chars):
    """Return high-confidence contextual replacements for selected PUA chars."""
    replacements = {}
    for idx, ch in enumerate(chars):
        if ch == '\uf0b7':
            next_ch = _next_nonspace_char(chars, idx)
            if _is_line_start_after_indent(chars, idx) and next_ch and next_ch not in "\r\n":
                repl = "-" if idx + 1 < len(chars) and chars[idx + 1].isspace() else "- "
                replacements[idx] = (repl, "pua_f0b7_list_bullet")
        elif ch == '\uf07e':
            if (NUMERIC_RANGE_LEFT_RE.search(_prev_window(chars, idx))
                    and NUMERIC_RANGE_RIGHT_RE.search(_next_window(chars, idx))):
                replacements[idx] = ("~", "pua_f07e_numeric_range")
        elif ch in PUA_KNOWN_NOISE_CHARS:
            replacements[idx] = ("", "pua_known_noise")
        elif ch == '\ue010':
            if (PUA_DOT_LEFT_RE.search(_prev_window(chars, idx, 1))
                    and PUA_DOT_RIGHT_RE.search(_next_window(chars, idx, 4))):
                replacements[idx] = ("．", "pua_e010_dot")
        elif ch == '\ue011':
            if (PUA_HYPHEN_LEFT_RE.search(_prev_window(chars, idx, 1))
                    and PUA_HYPHEN_RIGHT_RE.search(_next_window(chars, idx, 1))):
                replacements[idx] = ("-", "pua_e011_name_hyphen")
        elif ch == '\ue10b':
            if (PUA_HYPHEN_LEFT_RE.search(_prev_window(chars, idx, 1))
                    and PUA_HYPHEN_RIGHT_RE.search(_next_window(chars, idx, 1))):
                replacements[idx] = ("'", "pua_e10b_apostrophe")
        elif ch in ('\ue11a', '\ue11b', '\ue11c'):
            replacements[idx] = ("", "pua_ebook_footnote_marker")
        elif ch == '\ue618':
            has_following_space = idx + 1 < len(chars) and chars[idx + 1].isspace()
            if _is_line_start_after_indent(chars, idx):
                repl = "-" if has_following_space else "- "
            else:
                repl = "\n-" if has_following_space else "\n- "
            replacements[idx] = (repl, "pua_e618_bullet")
        elif ch == '\ue1bd':
            if _next_window(chars, idx, 1) == "束":
                replacements[idx] = ("约", "pua_e1bd_yue")
    return replacements


def find_forbidden_string_spans(text):
    """Find forbidden literal strings and safe literal escape bugs."""
    spans = []
    seen = set()
    for needle in FORBIDDEN_STRINGS:
        if not needle:
            continue
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx == -1:
                break
            key = (idx, idx + len(needle))
            if key not in seen:
                seen.add(key)
                spans.append((idx, idx + len(needle), needle))
            start = idx + len(needle)
    for match in LITERAL_ESCAPE_PATTERN.finditer(text):
        key = match.span()
        if key not in seen:
            seen.add(key)
            spans.append((match.start(), match.end(), match.group()))
    return spans


def find_html_entity_spans(text):
    """Find named/decimal/hex HTML entities."""
    return [
        (match.start(), match.end(), match.group())
        for match in HTML_ENTITY_PATTERN.finditer(text)
    ]


def _is_math_flood_symbol(ch):
    cat = unicodedata.category(ch)
    return cat in ("Sm", "So") or ch in MATH_SYMBOL_FLOOD_EXTRA_CHARS


def _scan_math_symbol_flood(chars, threshold=MATH_SYMBOL_FLOOD_THRESHOLD):
    """Find runs of the same symbol/separator char repeated at least threshold times."""
    to_delete = set()
    i = 0
    n = len(chars)
    while i < n:
        if not _is_math_flood_symbol(chars[i]):
            i += 1
            continue
        j = i + 1
        while j < n and chars[j] == chars[i]:
            j += 1
        if j - i >= threshold:
            for idx in range(i, j):
                to_delete.add(idx)
        i = j
    return to_delete


def _is_suspicious_repeated_token(token):
    """Return True for identifier-like tokens with a strong internal flood."""
    return (
        any(ch.isalpha() for ch in token)
        and any(ch.isdigit() for ch in token)
        and REPEATED_NOISE_INTERNAL_RUN_PATTERN.search(token) is not None
    )


def _inside_fenced_code(text, offset):
    """Return True when offset is inside a Markdown triple-backtick block."""
    return text.count("```", 0, offset) % 2 == 1


def _scan_repeated_noise_token(chars, threshold=REPEATED_NOISE_TOKEN_MIN_REPEATS):
    """Find high-confidence suspicious tokens repeated consecutively."""
    text = "".join(chars)
    matches = list(REPEATED_NOISE_TOKEN_PATTERN.finditer(text))
    spans = []
    i = 0
    while i < len(matches):
        first = matches[i]
        token = first.group()
        if not _is_suspicious_repeated_token(token) or _inside_fenced_code(text, first.start()):
            i += 1
            continue

        count = 1
        end = first.end()
        j = i + 1
        while j < len(matches):
            current = matches[j]
            separator = text[end:current.start()]
            if (current.group() != token
                    or not separator
                    or REPEATED_NOISE_SEPARATOR_PATTERN.fullmatch(separator) is None):
                break
            count += 1
            end = current.end()
            j += 1

        if count >= threshold:
            # Consume trailing horizontal whitespace so a following heading does
            # not retain an artificial leading space after deletion.
            clean_end = end
            while clean_end < len(text) and text[clean_end] in (" ", "\t"):
                clean_end += 1
            replacement = ""
            if (first.start() > 0 and clean_end < len(text)
                    and not text[first.start() - 1].isspace()
                    and not text[clean_end].isspace()):
                replacement = " "
            spans.append((first.start(), clean_end, token, count, replacement))
            i = j
        else:
            i += 1
    return spans


def _scan_hex_blob(chars):
    """Find long hex/binary dump spans while preserving hashes and constants."""
    text = "".join(chars)
    spans = []
    for match in HEX_CONTIGUOUS_PATTERN.finditer(text):
        length = match.end() - match.start()
        if length >= HEX_BLOB_MIN_CONTIGUOUS:
            spans.append(match.span())

    for match in HEX_WRAPPED_PATTERN.finditer(text):
        value = match.group()
        hex_count = sum(ch in "0123456789abcdefABCDEF" for ch in value)
        if hex_count < HEX_BLOB_MIN_WRAPPED_HEX:
            continue
        if hex_count / max(1, len(value)) < HEX_BLOB_MIN_RATIO:
            continue
        line_hex_lengths = [
            sum(ch in "0123456789abcdefABCDEF" for ch in line)
            for line in re.split(r"\r?\n", value)
        ]
        if max(line_hex_lengths or [0]) < HEX_BLOB_MIN_LINE_HEX:
            continue
        spans.append(match.span())

    to_delete = set()
    for start, end in spans:
        to_delete.update(range(start, end))
    return to_delete


def _effective_replacement_char(chars, replacements, idx):
    if idx in replacements:
        _rule, repl, _reason = replacements[idx]
        return repl
    return chars[idx]


def _iter_effective_units(chars, replacements):
    for idx, _ch in enumerate(chars):
        effective = _effective_replacement_char(chars, replacements, idx)
        if not effective:
            continue
        for pos, out_ch in enumerate(effective):
            yield idx, pos, out_ch


def _mark_unit(unit_deletions, idx, pos):
    unit_deletions.setdefault(idx, set()).add(pos)


def _apply_unit_deletions(chars, replacements, unit_deletions, new_rule, reason):
    for idx, positions in unit_deletions.items():
        effective = _effective_replacement_char(chars, replacements, idx)
        if not effective:
            continue
        new_effective = "".join(
            ch for pos, ch in enumerate(effective) if pos not in positions
        )
        if idx in replacements:
            rule, _repl, old_reason = replacements[idx]
            combined_reason = old_reason if reason in old_reason else old_reason + "+" + reason
            replacements[idx] = (rule, new_effective, combined_reason)
        elif new_effective == "":
            replacements[idx] = (new_rule, "", reason)


def _scan_line_boundary_space(chars, replacements):
    """Find spaces/tabs that would sit at the end of a line after prior cleaning."""
    units = list(_iter_effective_units(chars, replacements))
    unit_deletions = {}
    idx = 0
    while idx < len(units):
        if units[idx][2] not in (" ", "\t"):
            idx += 1
            continue
        run_start = idx
        while idx < len(units) and units[idx][2] in (" ", "\t"):
            idx += 1
        if idx == len(units) or units[idx][2] == "\n":
            for del_idx in range(run_start, idx):
                src_idx, src_pos, _out_ch = units[del_idx]
                _mark_unit(unit_deletions, src_idx, src_pos)
    return unit_deletions


def _scan_blank_line_collapse(chars, replacements, max_newlines=2):
    """Find extra consecutive newlines after prior cleaning."""
    unit_deletions = {}
    consecutive = 0
    for idx, pos, out_ch in _iter_effective_units(chars, replacements):
        if out_ch == "\n":
            consecutive += 1
            if consecutive > max_newlines:
                _mark_unit(unit_deletions, idx, pos)
        else:
            consecutive = 0
    return unit_deletions


def clean_text(text, rules=DEFAULT_RULES):
    """Clean text according to enabled rules.

    - variation_selector: removes the selector only.
    - styled_math_alnum: normalizes styled math letters/digits to plain forms with NFKC.
    - abnormal_space: U+00A0/2003/3000 → replaced with plain space; U+202F/2009 → removed.
    - bidi_control: bidi embeddings/overrides/isolates and LTR/RTL marks → removed.
    - pua_known_noise: exact known PUA noise U+F8FF/U+F04A → removed.
    - decorative_symbol: box/block/geometric/misc/dingbat symbols → removed.
    - decorative_combining: exact decorative combining-mark whitelist → removed.
    - control_invisible: BOM / zero-width / directional marks → removed.
    - line_separator: U+2028 → LF; U+2029 → blank-line paragraph break.
    - pua_contextual: selected PUA characters are repaired only in high-confidence contexts.
    - html_entity: named/decimal/hex HTML entities → delete spans.
    - forbidden_strings: HTML residues / training tokens / bad literal escapes → delete spans.
    - repeated_noise_token: delete suspicious alphanumeric tokens repeated ≥5 times.
    - math_symbol_flood: ≥4 repeated same symbol/separator chars → delete run.
    - line_boundary_space: delete spaces/tabs before LF or end of text.
    - blank_line_collapse: collapse runs of 3+ LF characters to 2 LF characters.
    Optional legacy rules are available via --rules but are not part of default V6:
    - combining_decoration: a base char followed by ≥2 consecutive combining marks
      (Mn/Mc/Me, excluding variation selectors) → delete the combining marks, keep the base.
      Protects résumé (NFD: e+0301, run=1) and x̄ (x+0304, run=1) from being stripped.

    Returns:
        cleaned_text (str), change_items (list of dicts with offset/char/rule/replacement)
    """
    enabled = set(rules)
    chars = list(text)
    # Maps offset → (rule, replacement_char, reason)
    replacements = {}

    for idx, ch in enumerate(chars):
        rule, repl = classify_char(ch)
        if (rule == "variation_selector" and rule not in enabled and "emoji_all" in enabled
                and idx > 0 and is_emoji_variation_base(chars[idx - 1])):
            replacements[idx] = ("emoji_all", "", "emoji_variation_selector")
            prev = idx - 1
            if prev not in replacements:
                replacements[prev] = ("emoji_all", "", "emoji_variation_base")
            continue
        if rule == "decorative_symbol" and rule not in enabled and "emoji_all" in enabled:
            if _in_ranges(ord(ch), EMOJI_RANGES):
                rule, repl = "emoji_all", ""
        elif rule == "bidi_control" and rule not in enabled and "control_invisible" in enabled:
            if ch in CONTROL_INVISIBLE_CHARS:
                rule, repl = "control_invisible", ""
        if rule not in enabled:
            continue
        replacements[idx] = (rule, repl, "matched")
        if (rule == "variation_selector" and "emoji_all" in enabled
                and idx > 0 and is_emoji_variation_base(chars[idx - 1])):
            prev = idx - 1
            if prev not in replacements:
                replacements[prev] = ("emoji_all", "", "emoji_variation_base")
        if rule == "emoji_all" and ch in ('\u20e3', '\u20e4'):
            if idx > 0 and chars[idx - 1] in ('\ufe0e', '\ufe0f') and (idx - 1) not in replacements:
                replacements[idx - 1] = ("emoji_all", "", "keycap_variation_selector")
            prev = _previous_non_replaced_base(chars, idx, replacements)
            if prev is not None and chars[prev] in KEYCAP_BASE_CHARS and prev not in replacements:
                replacements[prev] = ("emoji_all", "", "keycap_base")
    if "pua_contextual" in enabled:
        pua_replacements = _scan_pua_contextual(chars)
        for idx, (repl, reason) in pua_replacements.items():
            if idx not in replacements:
                replacements[idx] = ("pua_contextual", repl, reason)
    if "combining_decoration" in enabled:
        deco_idxs = _scan_combining_decoration(chars, COMBINING_DECORATION_RUN_THRESHOLD)
        for idx in deco_idxs:
            if idx not in replacements:
                replacements[idx] = ("combining_decoration", "", "matched")
    if "forbidden_strings" in enabled:
        for start, end, _matched in find_forbidden_string_spans(text):
            for idx in range(start, end):
                if idx not in replacements:
                    replacements[idx] = ("forbidden_strings", "", "matched")
    if "repeated_noise_token" in enabled:
        for start, end, _token, count, span_replacement in _scan_repeated_noise_token(chars):
            reason = "repeat_count={}".format(count)
            for idx in range(start, end):
                if idx not in replacements:
                    replacement = span_replacement if idx == start else ""
                    replacements[idx] = ("repeated_noise_token", replacement, reason)
    if "hex_blob" in enabled:
        hex_idxs = _scan_hex_blob(chars)
        for idx in hex_idxs:
            if idx not in replacements:
                replacements[idx] = ("hex_blob", "", "long_hex_blob")
    if "html_entity" in enabled:
        for start, end, _matched in find_html_entity_spans(text):
            for idx in range(start, end):
                if idx not in replacements:
                    replacements[idx] = ("html_entity", "", "matched")
    if "math_symbol_flood" in enabled:
        flood_idxs = _scan_math_symbol_flood(chars, MATH_SYMBOL_FLOOD_THRESHOLD)
        for idx in flood_idxs:
            if idx not in replacements:
                replacements[idx] = ("math_symbol_flood", "", "matched")
    if "line_boundary_space" in enabled:
        boundary_deletions = _scan_line_boundary_space(chars, replacements)
        _apply_unit_deletions(
            chars, replacements, boundary_deletions,
            "line_boundary_space", "line_boundary",
        )
    if "blank_line_collapse" in enabled:
        blank_line_deletions = _scan_blank_line_collapse(chars, replacements)
        _apply_unit_deletions(
            chars, replacements, blank_line_deletions,
            "blank_line_collapse", "collapse_extra_lf",
        )

    removed = []
    for idx in sorted(replacements):
        rule, repl, reason = replacements[idx]
        info = char_info(chars[idx], rule, repl)
        info["offset"] = idx
        info["reason"] = reason
        removed.append(info)

    output = []
    for idx, ch in enumerate(chars):
        if idx in replacements:
            _, repl, _ = replacements[idx]
            if repl:  # non-empty → insert replacement character
                output.append(repl)
            # empty → skip (delete)
        else:
            output.append(ch)

    return "".join(output), removed


def _parse_rules(value):
    rules = tuple(item.strip() for item in value.split(",") if item.strip())
    if not rules:
        return DEFAULT_RULES
    if "all" in {rule.lower() for rule in rules}:
        valid = set(ALL_RULES)
        extras = []
        for rule in rules:
            if rule.lower() == "all":
                continue
            if rule not in valid:
                raise argparse.ArgumentTypeError(
                    "unknown rule(s): {}. valid: {}".format(
                        rule, ",".join(sorted(valid))
                    )
                )
            if rule not in DEFAULT_RULES and rule not in extras:
                extras.append(rule)
        return DEFAULT_RULES + tuple(extras)
    valid = set(ALL_RULES)
    unknown = set(rules) - valid
    if unknown:
        raise argparse.ArgumentTypeError(
            "unknown rule(s): {}. valid: {}".format(
                ",".join(sorted(unknown)), ",".join(sorted(valid))
            )
        )
    return rules or DEFAULT_RULES


def _meta_replacement_value(value):
    """Convert legacy repr replacement strings to plain replacement text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    try:
        parsed = ast.literal_eval(value)
    except Exception:
        return value
    return parsed if isinstance(parsed, str) else value


def _meta_text_preview(value, limit=META_TEXT_PREVIEW_CHARS):
    if len(value) <= limit:
        return value
    half = max(1, limit // 2)
    return value[:half] + "..." + value[-half:]


def _compact_span(span_items):
    first = span_items[0]
    start = first["offset"]
    end = span_items[-1]["offset"] + 1
    original = "".join(item["char"] for item in span_items)
    replacement = "".join(_meta_replacement_value(item.get("replacement", "")) for item in span_items)
    codepoint_counts = Counter(item["codepoint"] for item in span_items)
    return {
        "field": first["field"],
        "start": start,
        "end": end,
        "count": len(span_items),
        "action": "replace" if replacement else "delete",
        "original_preview": _meta_text_preview(original),
        "replacement_preview": _meta_text_preview(replacement),
        "codepoint_counts": dict(codepoint_counts),
        "reasons": sorted(set(item["reason"] for item in span_items)),
    }


def _build_spans_by_rule(row_removed, max_meta_spans):
    """Group character-level changes into contiguous spans under each rule."""
    by_rule = {}
    sorted_items = sorted(row_removed, key=lambda item: (item["rule"], item["field"], item["offset"]))
    current = []

    def flush():
        if not current:
            return
        rule = current[0]["rule"]
        info = by_rule.setdefault(rule, {
            "count": 0,
            "field_counts": {},
            "span_count": 0,
            "spans_truncated": False,
            "spans": [],
        })
        span = _compact_span(current)
        info["count"] += span["count"]
        info["field_counts"][span["field"]] = info["field_counts"].get(span["field"], 0) + span["count"]
        info["span_count"] += 1
        if max_meta_spans == 0 or len(info["spans"]) < max_meta_spans:
            info["spans"].append(span)
        else:
            info["spans_truncated"] = True

    for item in sorted_items:
        if (current
                and item["rule"] == current[-1]["rule"]
                and item["field"] == current[-1]["field"]
                and item["offset"] == current[-1]["offset"] + 1):
            current.append(item)
        else:
            flush()
            current = [item]
    flush()
    return by_rule


def _build_char_changes(row_removed, max_meta_changes):
    if max_meta_changes < 0:
        max_meta_changes = DEFAULT_MAX_META_CHANGES
    if max_meta_changes == 0:
        return row_removed, False
    return row_removed[:max_meta_changes], len(row_removed) > max_meta_changes


def _build_cleaning_meta(
        line_no, fields, rules, row_removed, max_meta_changes, max_meta_spans,
        changed, meta_detail):
    """Build per-record provenance metadata for JSONL cleaning."""
    rule_counts = Counter(item["rule"] for item in row_removed)
    field_counts = Counter(item["field"] for item in row_removed)
    changed_fields = sorted(field_counts)

    meta = {
        "version": CLEANING_META_VERSION,
        "schema": "compact_by_rule_v1",
        "line_no": line_no,
        "changed": changed,
        "fields": list(fields),
        "rules": list(rules),
        "changed_fields": changed_fields,
        "change_count": len(row_removed),
        "rule_counts": dict(rule_counts),
        "field_counts": dict(field_counts),
    }
    if meta_detail in ("spans", "both"):
        meta["by_rule"] = _build_spans_by_rule(row_removed, max_meta_spans)
    if meta_detail in ("chars", "both"):
        changes, truncated = _build_char_changes(row_removed, max_meta_changes)
        meta["changes_truncated"] = truncated
        meta["changes"] = changes
    return meta


def _attach_cleaning_meta(record, cleaning_meta):
    """Attach cleaning metadata without overwriting a non-dict source meta field."""
    existing_meta = record.get("meta")
    if existing_meta is None:
        record["meta"] = {CLEANING_META_KEY: cleaning_meta}
    elif isinstance(existing_meta, dict):
        existing_meta[CLEANING_META_KEY] = cleaning_meta
    else:
        record[FALLBACK_CLEANING_META_KEY] = cleaning_meta


def _clean_jsonl_line(task):
    (line_no, line, fields, rules, add_cleaning_meta, max_meta_changes,
     max_meta_spans, meta_detail) = task
    stats = Counter()
    stats["total"] = 1
    example = None
    issue_line = None

    try:
        record = json.loads(line)
    except Exception as exc:
        stats["bad_json"] += 1
        example = {
            "line_no": line_no,
            "error": "bad_json:{}".format(str(exc)[:200]),
        }
        return line_no, line, dict(stats), issue_line, example

    changed = False
    row_changes = []
    if isinstance(record, dict):
        for field in fields:
            value = record.get(field)
            if not isinstance(value, str):
                continue
            cleaned, changes = clean_text(value, rules)
            if changes:
                record[field] = cleaned
                changed = True
                stats["changed_fields"] += 1
                row_changes.extend({"field": field, **item} for item in changes)
                for item in changes:
                    stats["changed_chars"] += 1
                    stats["changed_" + item["rule"]] += 1

    if add_cleaning_meta and isinstance(record, dict):
        _attach_cleaning_meta(record, _build_cleaning_meta(
            line_no, fields, rules, row_changes, max_meta_changes,
            max_meta_spans, changed, meta_detail,
        ))
        stats["meta_rows"] += 1

    if changed:
        stats["changed_rows"] += 1
        issue_line = line_no
        example = {
            "line_no": line_no,
            "change_count": len(row_changes),
            "changes": row_changes[:20],
        }

    return line_no, json.dumps(record, ensure_ascii=False) + "\n", dict(stats), issue_line, example


def clean_jsonl(
        input_path, output_path, fields, rules, dry_run=False, max_examples=20,
        add_cleaning_meta=False, max_meta_changes=DEFAULT_MAX_META_CHANGES,
        max_meta_spans=DEFAULT_MAX_META_SPANS, meta_detail=DEFAULT_META_DETAIL,
        workers=1, chunksize=16, max_in_flight=None):
    """Clean configured fields in a JSONL file."""
    stats = Counter()
    examples = []
    issue_rows = []

    out = None
    if not dry_run:
        out = open(output_path, "w", encoding="utf-8")

    try:
        with open(input_path, "r", encoding="utf-8") as fin:
            task_iter = (
                (line_no, line, fields, rules, add_cleaning_meta, max_meta_changes,
                 max_meta_spans, meta_detail)
                for line_no, line in enumerate(fin, 1)
            )
            if workers <= 1:
                result_iter = map(_clean_jsonl_line, task_iter)
                for _, output_line, row_stats, issue_line, example in result_iter:
                    stats.update(row_stats)
                    if issue_line is not None and len(issue_rows) < 1000:
                        issue_rows.append(issue_line)
                    if example is not None and len(examples) < max_examples:
                        examples.append(example)
                    if out:
                        out.write(output_line)
            else:
                max_in_flight = max_in_flight or workers * 2
                max_in_flight = max(1, max_in_flight)
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    pending = {}
                    completed = {}
                    next_write_line = 1

                    def submit(task):
                        future = executor.submit(_clean_jsonl_line, task)
                        pending[future] = task[0]

                    def drain_one():
                        nonlocal next_write_line
                        done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                        for future in done:
                            line_no = pending.pop(future)
                            completed[line_no] = future.result()
                        while next_write_line in completed:
                            _line_no, output_line, row_stats, issue_line, example = completed.pop(next_write_line)
                            stats.update(row_stats)
                            if issue_line is not None and len(issue_rows) < 1000:
                                issue_rows.append(issue_line)
                            if example is not None and len(examples) < max_examples:
                                examples.append(example)
                            if out:
                                out.write(output_line)
                            next_write_line += 1

                    for task in task_iter:
                        submit(task)
                        while len(pending) >= max_in_flight:
                            drain_one()
                    while pending:
                        drain_one()
                    while next_write_line in completed:
                        _line_no, output_line, row_stats, issue_line, example = completed.pop(next_write_line)
                        stats.update(row_stats)
                        if issue_line is not None and len(issue_rows) < 1000:
                            issue_rows.append(issue_line)
                        if example is not None and len(examples) < max_examples:
                            examples.append(example)
                        if out:
                            out.write(output_line)
    finally:
        if out:
            out.close()

    return {
        "input": input_path,
        "output": "" if dry_run else output_path,
        "format": "jsonl",
        "dry_run": dry_run,
        "fields": list(fields),
        "rules": list(rules),
        "add_cleaning_meta": add_cleaning_meta,
        "meta_detail": meta_detail,
        "max_meta_changes": max_meta_changes,
        "max_meta_spans": max_meta_spans,
        "workers": workers,
        "chunksize": chunksize,
        "max_in_flight": max_in_flight,
        "stats": dict(stats),
        "issue_rows": issue_rows[:1000],
        "examples": examples,
    }


def _load_openpyxl():
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for xlsx cleaning: pip3 install openpyxl") from exc
    return openpyxl


def clean_xlsx(input_path, output_path, fields, rules, sheet_name=None, dry_run=False, max_examples=20):
    """Clean configured columns in an XLSX file by header name."""
    openpyxl = _load_openpyxl()
    wb = openpyxl.load_workbook(input_path)
    ws = wb[sheet_name] if sheet_name else wb.active

    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=False))
    headers = [cell.value for cell in header_row]
    field_to_col = {header: idx + 1 for idx, header in enumerate(headers)}
    missing = [field for field in fields if field not in field_to_col]
    if missing:
        raise ValueError("field(s) not found in header: {}".format(",".join(missing)))

    stats = Counter()
    examples = []
    issue_rows = []

    for row_idx in range(2, ws.max_row + 1):
        stats["total"] += 1
        changed = False
        row_changes = []
        for field in fields:
            cell = ws.cell(row=row_idx, column=field_to_col[field])
            value = cell.value
            if not isinstance(value, str):
                continue
            cleaned, changes = clean_text(value, rules)
            if changes:
                if not dry_run:
                    cell.value = cleaned
                changed = True
                stats["changed_fields"] += 1
                row_changes.extend({"field": field, **item} for item in changes)
                for item in changes:
                    stats["changed_chars"] += 1
                    stats["changed_" + item["rule"]] += 1

        if changed:
            stats["changed_rows"] += 1
            issue_rows.append(row_idx)
            if len(examples) < max_examples:
                examples.append({
                    "excel_row": row_idx,
                    "change_count": len(row_changes),
                    "changes": row_changes[:20],
                })

    if not dry_run:
        wb.save(output_path)
    wb.close()

    return {
        "input": input_path,
        "output": "" if dry_run else output_path,
        "format": "xlsx",
        "sheet": ws.title,
        "dry_run": dry_run,
        "fields": list(fields),
        "rules": list(rules),
        "stats": dict(stats),
        "issue_rows": issue_rows[:1000],
        "examples": examples,
    }


def _infer_format(path, explicit):
    if explicit:
        return explicit
    lower = path.lower()
    if lower.endswith(".jsonl"):
        return "jsonl"
    if lower.endswith(".xlsx"):
        return "xlsx"
    raise ValueError("cannot infer format from path: {}".format(path))


def _default_output_path(input_path, fmt):
    base, ext = os.path.splitext(input_path)
    if fmt == "jsonl":
        return base + ".cleaned" + ext
    if fmt == "xlsx":
        return base + ".cleaned" + ext
    return base + ".cleaned" + ext


def _assert_output_not_input(input_path, output_path, dry_run):
    if dry_run or not output_path:
        return
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ValueError("--output must be different from --input to avoid overwriting source data")


def _write_summary(summary, summary_path):
    if not summary_path:
        return
    parent = os.path.dirname(os.path.abspath(summary_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as fout:
        json.dump(summary, fout, ensure_ascii=False, indent=2)
        fout.write("\n")


def _run_optional_qc(input_path, output_dir, checks, workers, executor, progress_every):
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_long_dialog_quality_checks.py"),
        "--input", input_path,
        "--output-dir", output_dir,
        "--checks", checks,
        "--workers", str(max(1, workers)),
        "--executor", executor,
        "--no-passed-output",
        "--failed-records-output", os.path.join(output_dir, "failed_records.jsonl"),
        "--progress-every", str(max(1, progress_every)),
    ]
    subprocess.run(cmd, check=True)
    return {
        "input": input_path,
        "output_dir": output_dir,
        "summary": os.path.join(output_dir, "summary.json"),
        "issues": os.path.join(output_dir, "issues.jsonl"),
        "failed_records": os.path.join(output_dir, "failed_records.jsonl"),
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Clean V6 Unicode decoration/noise characters from JSONL or XLSX files."
    )
    parser.add_argument("--input", required=True, help="Input .jsonl or .xlsx path.")
    parser.add_argument("--output", default="", help="Output path. Defaults to <input>.cleaned.<ext>.")
    parser.add_argument("--format", choices=("jsonl", "xlsx"), default="", help="Input format override.")
    parser.add_argument(
        "--fields",
        default="content",
        help="Comma-separated JSON keys / Excel header names to clean. Default: content.",
    )
    parser.add_argument(
        "--rules",
        type=_parse_rules,
        default=DEFAULT_RULES,
        help="Comma-separated rules: {}. Default: all default V6 rules; legacy rules are opt-in.".format(RULE_HELP),
    )
    parser.add_argument("--sheet", default="", help="XLSX sheet name. Defaults to the active sheet.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only; do not write output file.")
    parser.add_argument("--summary", default="", help="Optional JSON summary output path.")
    parser.add_argument("--max-examples", type=int, default=20, help="Max examples to include in summary/stdout.")
    parser.add_argument(
        "--add-cleaning-meta",
        action="store_true",
        help="For JSONL output, add per-record meta.cleaning_v6 provenance.",
    )
    parser.add_argument(
        "--meta-detail",
        choices=("spans", "chars", "both"),
        default=DEFAULT_META_DETAIL,
        help="Cleaning meta detail: grouped spans, character changes, or both. Default: spans.",
    )
    parser.add_argument(
        "--max-meta-spans",
        type=int,
        default=DEFAULT_MAX_META_SPANS,
        help="Max contiguous spans saved per rule in each record meta; 0 means unlimited. Default: 0.",
    )
    parser.add_argument(
        "--max-meta-changes",
        type=int,
        default=DEFAULT_MAX_META_CHANGES,
        help="Max character-level changes saved when --meta-detail is chars/both; 0 means unlimited. Default: 500.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes for JSONL cleaning. Default: 1.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=16,
        help="Deprecated compatibility option. JSONL multiprocessing now uses bounded in-flight tasks.",
    )
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=0,
        help="Max queued JSONL records for multiprocessing. Defaults to workers * 2.",
    )
    parser.add_argument(
        "--qc",
        choices=("none", "before", "after", "both"),
        default="none",
        help="Optional JSONL QC stage. Default: none, so the command only cleans.",
    )
    parser.add_argument(
        "--qc-output-dir",
        default="",
        help="Base directory for optional QC output. Defaults to <output>.qc.",
    )
    parser.add_argument(
        "--qc-checks",
        default="all",
        help="Comma-separated optional QC checks: all,mojibake,repetition,dedup. Default: all.",
    )
    parser.add_argument(
        "--qc-workers",
        type=int,
        default=0,
        help="Workers for optional QC. Defaults to --workers for JSONL.",
    )
    parser.add_argument(
        "--qc-executor",
        choices=("process", "thread"),
        default="process",
        help="Executor for optional QC. Default: process.",
    )
    parser.add_argument(
        "--qc-progress-every",
        type=int,
        default=100,
        help="Progress interval for optional QC. Default: 100.",
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    fields = tuple(item.strip() for item in args.fields.split(",") if item.strip())
    if not fields:
        parser.error("--fields must not be empty")

    fmt = _infer_format(args.input, args.format)
    output = args.output or _default_output_path(args.input, fmt)
    cleaning_workers = max(1, args.workers)
    _assert_output_not_input(args.input, output, args.dry_run)

    qc_runs = {}
    if args.qc != "none":
        if fmt != "jsonl":
            parser.error("--qc is only supported for JSONL input")
        if args.dry_run:
            parser.error("--qc cannot be used with --dry-run because no cleaned output is written")
        qc_base = args.qc_output_dir or (output + ".qc")
        qc_workers = max(1, args.qc_workers or cleaning_workers)
        if args.qc in ("before", "both"):
            qc_runs["before"] = _run_optional_qc(
                args.input,
                os.path.join(qc_base, "before") if args.qc == "both" else qc_base,
                args.qc_checks,
                qc_workers,
                args.qc_executor,
                args.qc_progress_every,
            )

    if fmt == "jsonl":
        summary = clean_jsonl(
            args.input, output, fields, args.rules,
            dry_run=args.dry_run, max_examples=args.max_examples,
            add_cleaning_meta=args.add_cleaning_meta,
            max_meta_changes=args.max_meta_changes,
            max_meta_spans=args.max_meta_spans,
            meta_detail=args.meta_detail,
            workers=cleaning_workers,
            chunksize=max(1, args.chunksize),
            max_in_flight=args.max_in_flight or None,
        )
    elif fmt == "xlsx":
        summary = clean_xlsx(
            args.input, output, fields, args.rules,
            sheet_name=args.sheet or None,
            dry_run=args.dry_run, max_examples=args.max_examples,
        )
    else:
        raise AssertionError("unsupported format: {}".format(fmt))

    if args.qc != "none":
        qc_base = args.qc_output_dir or (output + ".qc")
        qc_workers = max(1, args.qc_workers or cleaning_workers)
        if args.qc in ("after", "both"):
            qc_runs["after"] = _run_optional_qc(
                output,
                os.path.join(qc_base, "after") if args.qc == "both" else qc_base,
                args.qc_checks,
                qc_workers,
                args.qc_executor,
                args.qc_progress_every,
            )
        summary["qc"] = qc_runs

    _write_summary(summary, args.summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
