# -*- coding: utf-8 -*-
"""
Self-contained repetition / flood / runaway-enum detectors.
Mirrors ../loop.py but with explicit imports so it can be shipped to
Hadoop streaming workers via `-file`.
"""
import re
from collections import Counter


# ===========================================================================
# Repetition N-gram checks
# ===========================================================================

# CJK character range (Chinese / Japanese kana / Korean). In CJK text there is
# no whitespace word boundary, so each character is its own token.
_CJK_RE = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af\uf900-\ufaff]')

# Standalone-punctuation tokens are filtered out after tokenization. We only
# drop tokens that are *entirely* CJK/full-width punctuation (。，、！？ ...
# plus the ideographic space). ASCII punctuation (including LaTeX structural
# characters like `{ } _ ^`) is **kept** so that the modified detector stays
# consistent with the original `text.split()` behaviour on LaTeX-heavy arXiv
# papers -- there, `\mathrm { x } _ { 2 }` splits into 7 tokens and the
# per-field repetition ratio must still be computable against the original
# n-gram count.
_PUNCT_CHARS = (
    '。，、；：！？“”‘’（）《》〈〉【】「」『』〔〕…—·～﹏'
    '\u3000'
)
_PUNCT_SET = set(_PUNCT_CHARS)


def _is_punct_token(tok):
    return bool(tok) and all(c in _PUNCT_SET for c in tok)


def _tokenize(text):
    """
    Mixed-language tokenizer:
      - CJK characters become individual tokens (one char per token).
      - Non-CJK runs are whitespace-split into word tokens.
      - Standalone-punctuation tokens are dropped.
    """
    tokens = []
    buf = []
    for ch in text:
        if _CJK_RE.match(ch):
            if buf:
                tokens.extend(''.join(buf).split())
                buf = []
            tokens.append(ch)
        else:
            buf.append(ch)
    if buf:
        tokens.extend(''.join(buf).split())
    return [t for t in tokens if not _is_punct_token(t)]


def _ngram_repetition_score(text, n=10, threshold=0.3):
    words = _tokenize(text)
    if len(words) < n * 3:
        return None

    ngrams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    total = len(ngrams)
    if total == 0:
        return None

    counts = Counter(ngrams)
    repeated_count = sum(c for ng, c in counts.items() if c > 1)
    ratio = repeated_count / total

    if ratio > threshold:
        most_common = counts.most_common(1)[0]
        top = most_common[0][:8]
        parts = [top[0]] if top else []
        for prev, cur in zip(top, top[1:]):
            sep = '' if (_CJK_RE.match(prev) and _CJK_RE.match(cur)) else ' '
            parts.append(sep + cur)
        return {
            "ratio": ratio,
            "total_ngrams": total,
            "repeated": repeated_count,
            "top_ngram_count": most_common[1],
            "top_ngram": ''.join(parts) + "...",
        }
    return None


def _runaway_counter_score(text):
    numbers = [(m.start(), m.end(), int(m.group()))
               for m in re.finditer(r'\b(\d{1,6})\b', text)]
    if len(numbers) < 10:
        return None

    inc_positions = set()
    i = 0
    while i < len(numbers) - 1:
        run = [i]
        j = i + 1
        while j < len(numbers):
            prev_val = numbers[run[-1]][2]
            curr_val = numbers[j][2]
            if curr_val == prev_val + 1:
                run.append(j)
                j += 1
            else:
                break
        if len(run) >= 5:
            for idx in run:
                inc_positions.add((numbers[idx][0], numbers[idx][1]))
            i = j
        else:
            i += 1

    if not inc_positions:
        return None

    result = []
    last_end = 0
    for start, end in sorted(inc_positions):
        result.append(text[last_end:start])
        result.append(' ')
        last_end = end
    result.append(text[last_end:])
    normalized = ''.join(result)

    return _ngram_repetition_score(normalized, n=10, threshold=0.55)


# Runaway empty enumeration detection
_RE_ENUM_LINE = re.compile(
    r'^\s*('
    r'\d+[\.\)]'
    r'|[０-９]+[．。）)]'
    r'|[一二三四五六七八九十百千零两]+[、。．\.]'
    r'|第[一二三四五六七八九十百千零两\d]+[、，,：:．\.]?'
    r'|[（(]\s*[\d一二三四五六七八九十]+\s*[)）]'
    r'|[\u2460-\u2473\u24eb-\u24ff]'
    r')\s*$'
)
_ENUM_CONSECUTIVE_THRESHOLD = 30
_ENUM_RATIO_THRESHOLD = 0.50
_ENUM_MIN_LINES = 20

# Token flood thresholds
_TOKEN_FLOOD_MIN_LEN = 200
_TOKEN_FLOOD_RATIO_SHORT = 0.80
_TOKEN_FLOOD_RATIO_LONG = 0.60


def check_repetition_ngram(record):
    """Detect fine-grained n-gram repetition."""
    for idx, msg in enumerate(record.get("messages", [])):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) < 200:
            continue

        visible = re.sub(r'^.*?</think>', '', content, count=1, flags=re.DOTALL).strip()
        if len(visible) < 200:
            continue

        rc = _runaway_counter_score(visible)
        if rc:
            return ("repetition_ngram:runaway_enum msg[{}] "
                    "ratio={:.2f} repeated={}/{} top='{}' x{}".format(
                        idx, rc['ratio'], rc['repeated'], rc['total_ngrams'],
                        rc['top_ngram'], rc['top_ngram_count']))

        r = _ngram_repetition_score(visible, n=10, threshold=0.75)
        if r:
            return ("repetition_ngram:10gram msg[{}] "
                    "ratio={:.2f} repeated={}/{} top='{}' x{}".format(
                        idx, r['ratio'], r['repeated'], r['total_ngrams'],
                        r['top_ngram'], r['top_ngram_count']))

        r = _ngram_repetition_score(visible, n=5, threshold=0.85)
        if r:
            return ("repetition_ngram:5gram msg[{}] "
                    "ratio={:.2f} repeated={}/{} top='{}' x{}".format(
                        idx, r['ratio'], r['repeated'], r['total_ngrams'],
                        r['top_ngram'], r['top_ngram_count']))

    return None


def check_token_flood(record):
    """Detect single token/char repeated endlessly."""
    for idx, msg in enumerate(record.get("messages", [])):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) < _TOKEN_FLOOD_MIN_LEN:
            continue

        visible = re.sub(r'^.*?</think>', '', content, count=1, flags=re.DOTALL).strip()
        if len(visible) < _TOKEN_FLOOD_MIN_LEN:
            continue

        tokens = _tokenize(visible)
        if len(tokens) < 20:
            continue

        counter = Counter(tokens)
        top_token, top_count = counter.most_common(1)[0]
        ratio = top_count / len(tokens)

        threshold = _TOKEN_FLOOD_RATIO_LONG if len(visible) > 1000 else _TOKEN_FLOOD_RATIO_SHORT

        if ratio >= threshold:
            return ("repetition_ngram:token_flood msg[{}] "
                    "token='{}' ratio={:.2f} ({}/{} tokens)".format(
                        idx, top_token[:20], ratio, top_count, len(tokens)))

    return None


def check_runaway_enum(record):
    """Detect runaway enumeration (1. 2. 3. ... going on forever with no content)."""
    for idx, msg in enumerate(record.get("messages", [])):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) < 100:
            continue

        visible = re.sub(r'^.*?</think>', '', content, count=1, flags=re.DOTALL).strip()

        lines = visible.split('\n')
        if len(lines) < _ENUM_MIN_LINES:
            continue

        max_consecutive = 0
        current_run = 0
        total_enum_lines = 0

        for line in lines:
            if _RE_ENUM_LINE.match(line):
                current_run += 1
                total_enum_lines += 1
                max_consecutive = max(max_consecutive, current_run)
            else:
                current_run = 0

        if max_consecutive >= _ENUM_CONSECUTIVE_THRESHOLD:
            return ("repetition_ngram:runaway_enum msg[{}] "
                    "consecutive={} total_enum={}/{} lines".format(
                        idx, max_consecutive, total_enum_lines, len(lines)))

        if total_enum_lines >= _ENUM_MIN_LINES:
            enum_ratio = total_enum_lines / len(lines)
            if enum_ratio >= _ENUM_RATIO_THRESHOLD:
                return ("repetition_ngram:runaway_enum msg[{}] "
                        "ratio={:.2f} ({}/{} lines)".format(
                            idx, enum_ratio, total_enum_lines, len(lines)))

    return None
