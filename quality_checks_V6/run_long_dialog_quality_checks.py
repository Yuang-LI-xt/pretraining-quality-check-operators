#!/usr/bin/env python3
"""Run NExtLong rule-based quality checks over a JSONL long-dialog file."""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QUALITY_CHECKS_DIR = SCRIPT_DIR

_CONFIG = None
_MOJIBAKE_DETECTOR = None
_CHECK_REPETITION_NGRAM = None
_CHECK_TOKEN_FLOOD = None
_CHECK_RUNAWAY_ENUM = None
_DETECT_SPLIT_CONTENT = None
_CONTEXT_CHARS = 120


def _parse_checks(value):
    checks = {item.strip().lower() for item in value.split(",") if item.strip()}
    if not checks or "all" in checks:
        return {"mojibake", "repetition", "dedup"}
    unknown = checks - {"mojibake", "repetition", "dedup"}
    if unknown:
        raise argparse.ArgumentTypeError(
            "unknown check(s): {}".format(",".join(sorted(unknown)))
        )
    return checks


def _init_worker(config):
    """Import check modules once per worker."""
    global _CONFIG
    global _MOJIBAKE_DETECTOR
    global _CHECK_REPETITION_NGRAM
    global _CHECK_TOKEN_FLOOD
    global _CHECK_RUNAWAY_ENUM
    global _DETECT_SPLIT_CONTENT

    _CONFIG = config
    sys.path.insert(0, QUALITY_CHECKS_DIR)
    sys.path.insert(0, os.path.join(QUALITY_CHECKS_DIR, "repetition_check"))
    sys.path.insert(0, os.path.join(QUALITY_CHECKS_DIR, "text_sub_dedup"))

    if "mojibake" in config["checks"]:
        from mojibake_detect.detector import MojibakeDetector

        _MOJIBAKE_DETECTOR = MojibakeDetector()

    if "repetition" in config["checks"]:
        from loop import check_repetition_ngram, check_runaway_enum, check_token_flood

        _CHECK_REPETITION_NGRAM = check_repetition_ngram
        _CHECK_TOKEN_FLOOD = check_token_flood
        _CHECK_RUNAWAY_ENUM = check_runaway_enum

    if "dedup" in config["checks"]:
        from continuous_dedup_mapper_v7 import detect_split_content

        _DETECT_SPLIT_CONTENT = detect_split_content


def _get_text(record):
    for key in ("content", "text", "tgt"):
        value = record.get(key)
        if isinstance(value, str):
            return value, key
    return "", "content"


def _get_lang(record, text):
    meta = record.get("meta")
    lang = ""
    if isinstance(meta, dict):
        lang = str(meta.get("lang") or "")
    lang = lang or str(record.get("lang") or "")
    lang = lang.lower()
    if lang.startswith("en"):
        return "en"
    if lang.startswith(("zh", "cn", "ch")):
        return "cn"

    sample = text[:2000]
    cjk_count = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "cn" if cjk_count / max(len(sample), 1) > 0.1 else "en"


def _text_slice(text, start, end, context_chars=None):
    context_chars = _CONTEXT_CHARS if context_chars is None else context_chars
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    context_start = max(0, start - context_chars)
    context_end = min(len(text), end + context_chars)
    return {
        "offset": [start, end],
        "text": text[start:end],
        "context_offset": [context_start, context_end],
        "context": text[context_start:context_end],
    }


def _char_info(text, index):
    ch = text[index]
    return {
        "index": index,
        "char": ch,
        "repr": repr(ch),
        "unicode_escape": ch.encode("unicode_escape").decode("ascii"),
        "codepoint": "U+{:04X}".format(ord(ch)),
        "name": unicodedata.name(ch, "<unnamed>"),
        "category": unicodedata.category(ch),
    }


def _hit_char_infos(text, base_start, detail, max_chars=50):
    chars = []
    seen = set()
    for rule, rule_detail in detail.items():
        positions = rule_detail.get("hit_positions") if isinstance(rule_detail, dict) else None
        if not positions:
            continue
        for rel_idx, marker in enumerate(positions):
            if marker != "1":
                continue
            abs_idx = base_start + rel_idx
            if abs_idx in seen or abs_idx < 0 or abs_idx >= len(text):
                continue
            info = _char_info(text, abs_idx)
            info["rule"] = rule
            chars.append(info)
            seen.add(abs_idx)
            if len(chars) >= max_chars:
                return chars
    return chars


def _summarize_mojibake(result, text):
    per_rule = Counter()
    highlights = []
    for hit in result.get("hits", []):
        offset = hit.get("offset")
        hit_rules = Counter()
        hit_detail = hit.get("detail", {})
        for rule, detail in hit_detail.items():
            hit_num = int(detail.get("hit_num") or 0)
            if hit_num > 0:
                per_rule[rule] += hit_num
                hit_rules[rule] = hit_num
        if isinstance(offset, list) and len(offset) == 2 and hit_rules:
            highlight = _text_slice(text, offset[0], offset[1])
            chars = _hit_char_infos(text, offset[0], hit_detail)
            highlight.update({
                "rule": "mojibake",
                "reason": "mojibake rules: {}".format(
                    ",".join("{}={}".format(rule, count) for rule, count in hit_rules.most_common())
                ),
                "detail": {
                    "rules": dict(hit_rules),
                    "chars": chars,
                },
            })
            highlights.append(highlight)
    return sum(per_rule.values()), per_rule, highlights


def _check_mojibake(text):
    result = _MOJIBAKE_DETECTOR.exec_text(text)
    if not result.get("hits"):
        return None, []

    total_hits, all_rules, highlights = _summarize_mojibake(result, text)
    top_rules = ",".join(
        "{}={}".format(rule, count)
        for rule, count in all_rules.most_common(4)
    )
    issue = "mojibake:hit_chars={} rules={} offsets={}".format(
        total_hits, top_rules, [item["offset"] for item in highlights[:3]]
    )
    return issue, highlights


def _find_repetition_offset(text, reason):
    candidates = []
    top_match = re.search(r"top='([^']+?)\.\.\.'", reason)
    if top_match:
        candidates.append(top_match.group(1).strip())
    token_match = re.search(r"token='([^']+)'", reason)
    if token_match:
        token = token_match.group(1).strip()
        candidates.extend([token, "{} {}".format(token, token)])

    for candidate in candidates:
        if not candidate:
            continue
        start = text.find(candidate)
        if start >= 0:
            return start, start + len(candidate), candidate
    return None


def _check_repetition(text):
    fake = {"messages": [{"role": "assistant", "content": text}]}
    issues = []
    highlights = []
    for checker in (
        _CHECK_REPETITION_NGRAM,
        _CHECK_TOKEN_FLOOD,
        _CHECK_RUNAWAY_ENUM,
    ):
        reason = checker(fake)
        if reason:
            issues.append(reason)
            located = _find_repetition_offset(text, reason)
            if located:
                start, end, _ = located
                highlight = _text_slice(text, start, end)
                highlight.update({
                    "rule": _issue_type(reason),
                    "reason": reason,
                    "detail": {},
                })
                highlights.append(highlight)
            else:
                highlights.append({
                    "rule": _issue_type(reason),
                    "reason": reason,
                    "offset": None,
                    "text": "",
                    "context_offset": None,
                    "context": "",
                    "detail": {},
                })
    return issues, highlights


def _check_dedup(text, lang):
    results = _DETECT_SPLIT_CONTENT(text, lang)
    if not results:
        return None, []
    first = dict(results[0])
    issue = "sub_dedup_continuous:count={} lang={} first={}".format(
        len(results),
        lang,
        json.dumps(first, ensure_ascii=False, separators=(",", ":")),
    )
    highlights = []
    for result in results[:20]:
        offset = result.get("offset")
        if not (isinstance(offset, list) and len(offset) == 2):
            continue
        repeat_offset = None
        info = result.get("info") if isinstance(result.get("info"), dict) else {}
        if isinstance(info.get("repeat_text_offset"), list) and len(info["repeat_text_offset"]) == 2:
            repeat_offset = info["repeat_text_offset"]
        highlight = _text_slice(text, offset[0], offset[1])
        repeat_text = ""
        if repeat_offset:
            repeat_text = text[repeat_offset[0]:repeat_offset[1]]
        highlight.update({
            "rule": "sub_dedup_continuous",
            "reason": (
                "continuous duplicate lang={} repeat_num={} repeat_text={!r}".format(
                    lang, result.get("score"), repeat_text
                )
            ),
            "detail": result,
        })
        highlights.append(highlight)
    return issue, highlights


def _issue_type(reason):
    return reason.split(":", 1)[0]


def _process_line(task):
    line_no, line = task
    try:
        record = json.loads(line)
    except Exception as exc:
        return {
            "line_no": line_no,
            "failed": True,
            "bad_json": True,
            "issues": ["bad_json:{}".format(str(exc)[:200])],
        }

    text, text_field = _get_text(record)
    lang = _get_lang(record, text)
    issues = []
    highlights = []

    try:
        if "mojibake" in _CONFIG["checks"]:
            issue, hit_items = _check_mojibake(text)
            if issue:
                issues.append(issue)
                highlights.extend(hit_items)

        if "repetition" in _CONFIG["checks"]:
            rep_issues, hit_items = _check_repetition(text)
            issues.extend(rep_issues)
            highlights.extend(hit_items)

        if "dedup" in _CONFIG["checks"]:
            issue, hit_items = _check_dedup(text, lang)
            if issue:
                issues.append(issue)
                highlights.extend(hit_items)
    except Exception as exc:
        issues.append("check_error:{}".format(str(exc)[:500]))

    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    report = {
        "line_no": line_no,
        "failed": bool(issues),
        "issues": issues,
        "issue_types": [_issue_type(issue) for issue in issues],
        "highlights": highlights,
        "text_field": text_field,
        "content_len": len(text),
        "lang": lang,
        "meta": meta,
    }
    if _CONFIG["excerpt_chars"] > 0 and issues:
        report["content_excerpt"] = text[: _CONFIG["excerpt_chars"]]
    return report


def _open_optional(path):
    if not path:
        return None
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return open(path, "w", encoding="utf-8")


def _default_passed_output(input_path):
    base, ext = os.path.splitext(os.path.abspath(input_path))
    if ext:
        return "{}_filtered{}".format(base, ext)
    return "{}_filtered.jsonl".format(os.path.abspath(input_path))


def _format_duration(seconds):
    if seconds is None or seconds == float("inf"):
        return "?"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "{}h{}m{}s".format(hours, minutes, secs)
    if minutes:
        return "{}m{}s".format(minutes, secs)
    return "{}s".format(secs)


def _format_bytes(num_bytes):
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "{:.1f}{}".format(value, unit) if unit != "B" else "{}B".format(int(value))
        value /= 1024


def _handle_result(result, line, writers, stats, reasons):
    stats["total"] += 1
    if result.get("bad_json"):
        stats["bad_json"] += 1

    if result["failed"]:
        stats["failed"] += 1
        writers["issues"].write(json.dumps(result, ensure_ascii=False) + "\n")
        if writers.get("failed_records") and not result.get("bad_json"):
            writers["failed_records"].write(line)
            if not line.endswith("\n"):
                writers["failed_records"].write("\n")
        for issue_type in result.get("issue_types", []):
            stats["issue_" + issue_type] += 1
            reasons[issue_type] += 1
    else:
        stats["passed"] += 1
        if writers.get("passed_records"):
            writers["passed_records"].write(line)
            if not line.endswith("\n"):
                writers["passed_records"].write("\n")


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Run NExtLong quality_checks over long_dialog-style JSONL."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input JSONL path.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for issues.jsonl and summary.json.",
    )
    parser.add_argument(
        "--checks",
        type=_parse_checks,
        default={"mojibake", "repetition", "dedup"},
        help="Comma-separated checks: all,mojibake,repetition,dedup.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=64,
        help="Number of parallel workers. Full-text checks can be memory-heavy.",
    )
    parser.add_argument(
        "--executor",
        choices=("process", "thread"),
        default="process",
        help="Use process for CPU-heavy local runs; thread is useful for debugging.",
    )
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=0,
        help="Bound queued records. Defaults to workers * 2.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process only N records.")
    parser.add_argument(
        "--excerpt-chars",
        type=int,
        default=500,
        help="Include this many leading content chars in issues.jsonl.",
    )
    parser.add_argument(
        "--passed-output",
        default=None,
        help="JSONL path for passed original records. Defaults to <input>.passed.jsonl.",
    )
    parser.add_argument(
        "--no-passed-output",
        action="store_true",
        help="Do not write passed/original records.",
    )
    parser.add_argument(
        "--failed-records-output",
        default="",
        help="Optional JSONL path for failed original records.",
    )
    parser.add_argument("--progress-every", type=int, default=100)
    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    issues_path = os.path.join(args.output_dir, "issues.jsonl")
    summary_path = os.path.join(args.output_dir, "summary.json")
    passed_output = "" if args.no_passed_output else (args.passed_output or _default_passed_output(args.input))
    config = {
        "checks": sorted(args.checks),
        "mode": "strict_full_text",
        "excerpt_chars": args.excerpt_chars,
    }

    workers = max(1, args.workers)
    max_in_flight = args.max_in_flight or workers * 2
    executor_cls = ProcessPoolExecutor if args.executor == "process" else ThreadPoolExecutor

    stats = Counter()
    reasons = Counter()
    start_time = time.time()

    writers = {
        "issues": open(issues_path, "w", encoding="utf-8"),
        "passed_records": _open_optional(passed_output),
        "failed_records": _open_optional(args.failed_records_output),
    }
    input_size = os.path.getsize(args.input) if os.path.exists(args.input) else 0
    bytes_submitted = 0
    eof_submitted = False

    def print_progress(force=False):
        if not force and (not args.progress_every or stats["total"] % args.progress_every != 0):
            return
        elapsed = max(time.time() - start_time, 1e-6)
        pct = (bytes_submitted / input_size * 100) if input_size else 0.0
        record_rate = stats["total"] / elapsed
        byte_rate = bytes_submitted / elapsed
        eta = None
        if byte_rate > 0 and input_size and not eof_submitted:
            eta = (input_size - bytes_submitted) / byte_rate
        print(
            "[QC] processed={} passed={} failed={} bad_json={} "
            "read={}/{} ({:.2f}%) rate={:.2f} rec/s {}/s eta={}".format(
                stats["total"],
                stats["passed"],
                stats["failed"],
                stats.get("bad_json", 0),
                _format_bytes(bytes_submitted),
                _format_bytes(input_size),
                pct,
                record_rate,
                _format_bytes(byte_rate),
                _format_duration(eta),
            ),
            file=sys.stderr,
            flush=True,
        )

    try:
        with executor_cls(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(config,),
        ) as executor, open(args.input, "r", encoding="utf-8") as fin:
            pending = {}

            def drain_one():
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    line_no, raw_line = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "line_no": line_no,
                            "failed": True,
                            "issues": ["worker_error:{}".format(str(exc)[:500])],
                            "issue_types": ["worker_error"],
                        }
                    _handle_result(result, raw_line, writers, stats, reasons)
                    print_progress()

            for line_no, line in enumerate(fin, 1):
                if args.limit and line_no > args.limit:
                    break
                bytes_submitted += len(line.encode("utf-8"))
                future = executor.submit(_process_line, (line_no, line))
                pending[future] = (line_no, line)
                while len(pending) >= max_in_flight:
                    drain_one()

            eof_submitted = True
            while pending:
                drain_one()
    finally:
        for writer in writers.values():
            if writer:
                writer.close()

    elapsed = time.time() - start_time
    summary = {
        "input": args.input,
        "output_dir": args.output_dir,
        "issues_path": issues_path,
        "passed_output": passed_output,
        "failed_records_output": args.failed_records_output,
        "config": config,
        "stats": dict(stats),
        "issue_reasons": dict(reasons),
        "elapsed_seconds": round(elapsed, 3),
        "records_per_second": round(stats["total"] / elapsed, 3) if elapsed else 0,
    }
    with open(summary_path, "w", encoding="utf-8") as fout:
        json.dump(summary, fout, ensure_ascii=False, indent=2)
        fout.write("\n")

    print_progress(force=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
