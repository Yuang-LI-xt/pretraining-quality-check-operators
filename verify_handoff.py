#!/usr/bin/env python3
"""Run dependency-free checks against an unpacked handoff package."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
REQUIRED = (
    "README.md",
    "PACKAGE_CONTENTS.md",
    "PACKAGE_MANIFEST.json",
    "QUALITY_CHECKS_V7_CODEX_PROMPT.txt",
    "quality_checks_V6/clean_variation_chars.py",
    "quality_checks_V6/quality_api.py",
    "quality_checks_V6/run_long_dialog_quality_checks.py",
    "quality_checks_V6/tests/test_repeated_noise_token.py",
    "examples/demo_input.jsonl",
)
FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"/Users/(?!\.\.\.)[^/\s]+/"),
    re.compile(r"/root/(?!\.\.\.)[^\s]+"),
    re.compile(r"/mnt/(?!\.\.\.)[^\s]+"),
)


def fail(message):
    raise SystemExit("FAIL: {}".format(message))


def main():
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail("missing required files: {}".format(", ".join(missing)))

    bad_artifacts = []
    for path in ROOT.rglob("*"):
        if path.is_dir() and path.name == "__pycache__":
            bad_artifacts.append(str(path.relative_to(ROOT)))
        elif path.is_file() and (path.suffix == ".pyc" or path.name == ".DS_Store"):
            bad_artifacts.append(str(path.relative_to(ROOT)))
    if bad_artifacts:
        fail("generated artifacts found: {}".format(", ".join(bad_artifacts)))

    source_files = tuple(ROOT.rglob("*.py"))
    text_files = (
        tuple(ROOT.rglob("*.md"))
        + tuple(ROOT.rglob("*.txt"))
        + tuple(ROOT.rglob("*.json"))
        + tuple(path for path in source_files if path != Path(__file__).resolve())
    )
    for path in text_files:
        content = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATH_PATTERNS:
            match = pattern.search(content)
            if match:
                fail("internal path {!r} found in {}".format(
                    match.group(), path.relative_to(ROOT)))

    for path in source_files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    sys.path.insert(0, str(ROOT))
    from quality_checks_V6 import clean_record

    source = {
        "id": "verify",
        "content": "𝙝𝙞 🏳‍🌈 a111111111 a111111111 a111111111 a111111111 a111111111",
    }
    result = clean_record(source, fields=["content"], add_meta=True)
    cleaned = result["record"]["content"]
    if "𝙝" in cleaned or "🏳" in cleaned or "a111111111" in cleaned:
        fail("synthetic cleaning case was not cleaned")
    if "cleaning_v6" not in result["record"].get("meta", {}):
        fail("cleaning_v6 metadata is missing")

    print("handoff package OK")
    print("compiled Python files:", len(source_files))
    print("required files:", len(REQUIRED))


if __name__ == "__main__":
    main()
