#!/usr/bin/env python3
"""Python and dependency-free HTTP interfaces for Quality Checks V6."""

import argparse
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from . import clean_variation_chars as cleaner
    from . import run_long_dialog_quality_checks as qc
except ImportError:
    import clean_variation_chars as cleaner
    import run_long_dialog_quality_checks as qc


API_VERSION = "v1"
DEFAULT_FIELDS = ("content",)
DEFAULT_CHECKS = ("mojibake", "repetition", "dedup")
DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024

_QC_INIT_LOCK = threading.Lock()
_QC_INITIALIZED = False


def _as_names(value, default):
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        values = tuple(item.strip() for item in value.split(",") if item.strip())
    elif isinstance(value, (list, tuple, set)):
        values = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        raise TypeError("expected a comma-separated string or a list of names")
    return values or tuple(default)


def _normalize_fields(fields):
    return _as_names(fields, DEFAULT_FIELDS)


def _normalize_rules(rules):
    names = _as_names(rules, cleaner.DEFAULT_RULES)
    if "all" in {name.lower() for name in names}:
        extras = []
        for name in names:
            if name.lower() == "all":
                continue
            if name not in cleaner.ALL_RULES:
                raise ValueError("unknown cleaning rule: {}".format(name))
            if name not in cleaner.DEFAULT_RULES and name not in extras:
                extras.append(name)
        return tuple(cleaner.DEFAULT_RULES) + tuple(extras)
    unknown = set(names) - set(cleaner.ALL_RULES)
    if unknown:
        raise ValueError(
            "unknown cleaning rule(s): {}".format(",".join(sorted(unknown)))
        )
    return names


def _normalize_checks(checks):
    names = _as_names(checks, DEFAULT_CHECKS)
    if "all" in {name.lower() for name in names}:
        return tuple(DEFAULT_CHECKS)
    unknown = set(names) - set(DEFAULT_CHECKS)
    if unknown:
        raise ValueError("unknown QC check(s): {}".format(",".join(sorted(unknown))))
    return names


def _ensure_qc_initialized():
    global _QC_INITIALIZED
    if _QC_INITIALIZED:
        return
    with _QC_INIT_LOCK:
        if _QC_INITIALIZED:
            return
        try:
            qc._init_worker({
                "checks": set(DEFAULT_CHECKS),
                "mode": "strict_full_text",
                "excerpt_chars": 0,
            })
        except ImportError as exc:
            raise RuntimeError(
                "QC dependency is unavailable; install quality_checks_V6/requirements.txt"
            ) from exc
        _QC_INITIALIZED = True


def clean_record(
        record, fields=None, rules=None, add_meta=True, meta_detail="spans",
        max_meta_spans=0, max_meta_changes=500, line_no=1):
    """Clean one record and return the cleaned record plus provenance."""
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")
    if meta_detail not in ("spans", "chars", "both"):
        raise ValueError("meta_detail must be one of: spans, chars, both")

    fields = _normalize_fields(fields)
    rules = _normalize_rules(rules)
    output = copy.deepcopy(record)
    row_changes = []

    for field in fields:
        value = output.get(field)
        if not isinstance(value, str):
            continue
        cleaned, changes = cleaner.clean_text(value, rules)
        if not changes:
            continue
        output[field] = cleaned
        row_changes.extend({"field": field, **item} for item in changes)

    cleaning_meta = cleaner._build_cleaning_meta(
        int(line_no),
        fields,
        rules,
        row_changes,
        int(max_meta_changes),
        int(max_meta_spans),
        bool(row_changes),
        meta_detail,
    )
    if add_meta:
        cleaner._attach_cleaning_meta(output, cleaning_meta)

    return {"record": output, "cleaning": cleaning_meta}


def check_record(record, checks=None, excerpt_chars=120, line_no=1):
    """Run selected V6 QC checks against one record."""
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")

    selected_checks = set(_normalize_checks(checks))
    excerpt_chars = max(0, int(excerpt_chars))
    _ensure_qc_initialized()

    text, text_field = qc._get_text(record)
    lang = qc._get_lang(record, text)
    issues = []
    highlights = []

    if "mojibake" in selected_checks:
        issue, hit_items = qc._check_mojibake(text)
        if issue:
            issues.append(issue)
            highlights.extend(hit_items)
    if "repetition" in selected_checks:
        rep_issues, hit_items = qc._check_repetition(text)
        issues.extend(rep_issues)
        highlights.extend(hit_items)
    if "dedup" in selected_checks:
        issue, hit_items = qc._check_dedup(text, lang)
        if issue:
            issues.append(issue)
            highlights.extend(hit_items)

    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    report = {
        "line_no": int(line_no),
        "failed": bool(issues),
        "issues": issues,
        "issue_types": [qc._issue_type(issue) for issue in issues],
        "highlights": highlights,
        "text_field": text_field,
        "content_len": len(text),
        "lang": lang,
        "checks": sorted(selected_checks),
        "meta": meta,
    }
    if excerpt_chars and issues:
        report["content_excerpt"] = text[:excerpt_chars]
    return report


def clean_and_check(
        record, fields=None, rules=None, checks=None, add_meta=True,
        meta_detail="spans", max_meta_spans=0, max_meta_changes=500,
        excerpt_chars=120, line_no=1):
    """Clean one record and run QC against the cleaned result."""
    cleaned = clean_record(
        record,
        fields=fields,
        rules=rules,
        add_meta=add_meta,
        meta_detail=meta_detail,
        max_meta_spans=max_meta_spans,
        max_meta_changes=max_meta_changes,
        line_no=line_no,
    )
    quality = check_record(
        cleaned["record"],
        checks=checks,
        excerpt_chars=excerpt_chars,
        line_no=line_no,
    )
    return {
        "record": cleaned["record"],
        "cleaning": cleaned["cleaning"],
        "quality": quality,
    }


def _clean_options(payload):
    return {
        "fields": payload.get("fields"),
        "rules": payload.get("rules"),
        "add_meta": payload.get("add_meta", True),
        "meta_detail": payload.get("meta_detail", "spans"),
        "max_meta_spans": payload.get("max_meta_spans", 0),
        "max_meta_changes": payload.get("max_meta_changes", 500),
        "line_no": payload.get("line_no", 1),
    }


class QualityAPIHandler(BaseHTTPRequestHandler):
    server_version = "QualityChecksV6/1.0"

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > self.server.max_body_bytes:
            raise ValueError(
                "request body exceeds {} bytes".format(self.server.max_body_bytes)
            )
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_json(200, {
                "status": "ok",
                "service": "quality_checks_v6",
                "version": cleaner.CLEANING_META_VERSION,
                "api_version": API_VERSION,
                "endpoints": [
                    "/v1/rules",
                    "/v1/clean",
                    "/v1/check",
                    "/v1/clean-and-check",
                ],
            })
            return
        if self.path == "/v1/rules":
            self._send_json(200, {
                "default_cleaning_rules": list(cleaner.DEFAULT_RULES),
                "optional_legacy_rules": list(cleaner.OPTIONAL_LEGACY_RULES),
                "qc_checks": list(DEFAULT_CHECKS),
                "defaults": {
                    "fields": list(DEFAULT_FIELDS),
                    "add_meta": True,
                    "meta_detail": "spans",
                },
            })
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        try:
            payload = self._read_json()
            record = payload.get("record")
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")

            if self.path == "/v1/clean":
                result = clean_record(record, **_clean_options(payload))
            elif self.path == "/v1/check":
                result = check_record(
                    record,
                    checks=payload.get("checks"),
                    excerpt_chars=payload.get("excerpt_chars", 120),
                    line_no=payload.get("line_no", 1),
                )
            elif self.path == "/v1/clean-and-check":
                result = clean_and_check(
                    record,
                    checks=payload.get("checks"),
                    excerpt_chars=payload.get("excerpt_chars", 120),
                    **_clean_options(payload),
                )
            else:
                self._send_json(404, {"error": "not_found"})
                return
            self._send_json(200, result)
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": "invalid_request", "message": str(exc)})
        except RuntimeError as exc:
            self._send_json(503, {"error": "service_unavailable", "message": str(exc)})
        except Exception as exc:
            self.log_error("request failed: %s", exc)
            self._send_json(500, {"error": "internal_error"})


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Serve Quality Checks V6 over HTTP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=DEFAULT_MAX_BODY_BYTES,
        help="Maximum JSON request size. Default: 50 MiB.",
    )
    return parser


def main():
    args = build_arg_parser().parse_args()
    server = ThreadingHTTPServer((args.host, args.port), QualityAPIHandler)
    server.max_body_bytes = max(1, args.max_body_bytes)
    print(
        "Quality Checks V6 API listening on http://{}:{}".format(
            args.host, server.server_port
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
