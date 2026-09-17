# Quality Checks V6.0

V6.0 contains command line and API interfaces for long-text data cleaning and
optional rule-based quality checking:

- `clean_variation_chars.py`: cleans character-level Unicode noise in JSONL or
  XLSX files.
- `run_long_dialog_quality_checks.py`: optionally reports remaining mojibake,
  repetition, and continuous duplicate text issues.
- `quality_api.py`: exposes single-record cleaning and QC as Python functions
  and dependency-free HTTP endpoints.

The default command **directly cleans data**. QC/finding issues is optional via
`--qc before`, `--qc after`, or `--qc both`.

Install the runtime dependencies before using QC or XLSX cleaning:

```bash
python3 -m pip install -r quality_checks_V6/requirements.txt
```

JSONL cleaning itself uses the Python standard library. `numpy` is required by
the mojibake QC detector, and `openpyxl` is required only for XLSX input.

## HTTP API

Start the service locally:

```bash
python3 quality_checks_V6/quality_api.py \
  --host 127.0.0.1 \
  --port 8000
```

Available endpoints:

- `GET /health`: service health and version.
- `GET /v1/rules`: V6 cleaning rules, optional legacy rules, and QC checks.
- `POST /v1/clean`: clean one JSON record.
- `POST /v1/check`: run QC against one JSON record.
- `POST /v1/clean-and-check`: clean one record, then run QC on the cleaned text.

Clean one record:

```bash
curl -X POST http://127.0.0.1:8000/v1/clean \
  -H 'Content-Type: application/json' \
  --data '{
    "record": {"id": "demo", "content": "A𝙤B😭﻿C"},
    "fields": ["content"],
    "rules": ["all"],
    "add_meta": true,
    "meta_detail": "spans"
  }'
```

Run selected QC checks:

```bash
curl -X POST http://127.0.0.1:8000/v1/check \
  -H 'Content-Type: application/json' \
  --data '{
    "record": {"content": "thank you thank you thank you thank you thank you"},
    "checks": ["dedup"],
    "excerpt_chars": 120
  }'
```

Clean and check in one request:

```bash
curl -X POST http://127.0.0.1:8000/v1/clean-and-check \
  -H 'Content-Type: application/json' \
  --data '{
    "record": {"content": "𝙝𝙚𝙡𝙡𝙤😭 thank you thank you thank you thank you thank you"},
    "fields": ["content"],
    "checks": ["mojibake", "repetition", "dedup"],
    "add_meta": true,
    "meta_detail": "spans"
  }'
```

The HTTP interface is record-oriented. Use the CLI for large JSONL/XLSX files
to avoid HTTP serialization overhead. The default request-body limit is 50 MiB
and can be changed with `--max-body-bytes`.

The server has no built-in authentication or TLS. Its default host is
`127.0.0.1`; when binding to `0.0.0.0`, place it behind an authenticated gateway
or run it only on a trusted internal network.

## Python API

The same operations can be called without HTTP:

```python
from quality_checks_V6 import clean_and_check, clean_record, check_record

cleaned = clean_record(
    {"id": "demo", "content": "A𝙤B😭﻿C"},
    fields=["content"],
    rules=["all"],
    add_meta=True,
)

quality = check_record(
    {"content": "thank you thank you thank you thank you thank you"},
    checks=["dedup"],
)

result = clean_and_check(
    {"content": "𝙝𝙚𝙡𝙡𝙤😭"},
    checks=["mojibake", "repetition", "dedup"],
)
```

## Quick Start: Clean JSONL On Server

Clean the `content` field with multiprocessing and write compact provenance
metadata:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.jsonl \
  --output /path/to/output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 32 \
  --max-in-flight 64 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /path/to/output.v6.clean_summary.json
```

Use fewer workers if records are very large or memory is tight. `--output` must
be different from `--input`.

## Optional QC Before/After Cleaning

Inspect original input before cleaning:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.jsonl \
  --output output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --add-cleaning-meta \
  --summary output.v6.clean_summary.json \
  --qc before \
  --qc-output-dir qc_before
```

Inspect cleaned output after cleaning:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.jsonl \
  --output output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --add-cleaning-meta \
  --summary output.v6.clean_summary.json \
  --qc after \
  --qc-output-dir qc_after
```

Compare both:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.jsonl \
  --output output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --add-cleaning-meta \
  --summary output.v6.clean_summary.json \
  --qc both \
  --qc-output-dir qc_compare
```

## V6.0 Default Cleaning Rules

- `variation_selector`: delete Unicode variation selectors.
- `styled_math_alnum`: normalize styled math letters/digits with NFKC, for
  example `𝙜𝙤𝙞𝙣𝙜 -> going`.
- `abnormal_space`: replace NBSP, EM SPACE, and full-width space with normal
  space; delete narrow NBSP and thin space.
- `bidi_control`: delete bidirectional text control characters:
  `U+202A-U+202E`, `U+2066-U+2069`, `U+200E`, `U+200F`.
- `pua_known_noise`: delete exact known PUA noise `U+F8FF` Apple logo and
  `U+F04A` font-private smiley/noise glyph.
- `pua_contextual`: keep the partial high-confidence PUA policy. Known repairs
  include list bullets, numeric ranges, and selected ebook punctuation. V6.0
  does **not** delete the whole PUA range.
- `decorative_symbol`: delete Box Drawing, Block Elements, Geometric Shapes,
  Misc Symbols, and Dingbats: `U+2500-U+27BF`, except semantic form symbols such
  as `□`, `☐`, `☑`, `☒`, `✓`, `✗`, `○`, and `●`.
- `decorative_combining`: delete only the exact decorative combining whitelist:
  `U+0336`, `U+033F`, `U+035C-U+0361`.
- `emoji_all`: delete emoji and emoji attachments such as ZWJ/keycap sequences.
- `control_invisible`: delete BOM, zero-width characters, word joiner, soft
  hyphen, and related invisible/control artifacts.
- `line_separator`: normalize `U+2028` to `\n` and `U+2029` to `\n\n`.
- `html_entity`: delete named/decimal/hex HTML entities, for example `&gt;`,
  `&lt;`, `&ndash;`, `&#123;`, `&#x1F;`.
- `forbidden_strings`: delete HTML residues, training tokens, and standalone
  literal escape bugs such as `\n`, `\t`, `\r` only when they are not followed by
  an English letter or `{`. LaTeX commands such as `\times`, `\theta`,
  `\right`, `\r{a}`, and file/path-like strings such as `C:\temp` are preserved.
- `math_symbol_flood`: delete runs of the same math/symbol character repeated 4
  or more times.
- `hex_blob`: delete high-confidence hexadecimal/binary dump blocks with at
  least 64 hex characters and a high hex ratio. `0x`-prefixed code constants
  remain protected by the boundary check; use a custom rule set when legitimate
  64-character hashes or machine data must be retained.
- `repeated_noise_token`: delete a suspicious alphanumeric token repeated
  consecutively 5 or more times. The token must contain both a letter and a
  digit plus an internal run of at least 4 identical characters, so normal
  words, pure numbers, and ordinary identifiers are preserved. Markdown fenced
  code blocks are excluded.
- `line_boundary_space`: delete spaces/tabs before newlines or end of text, so
  space-only blank lines like `\n \n` become real blank lines `\n\n`.
- `blank_line_collapse`: collapse 3 or more consecutive newlines to 2 newlines.

Run the focused repeated-noise regression tests from the directory containing
`quality_checks_V6`:

```bash
python3 -m unittest quality_checks_V6.tests.test_repeated_noise_token
```

Important: V6.0 does not delete all Unicode `Mn`/`Me` combining marks by
category. Hebrew points, Arabic vowels, Devanagari marks, and similar
language-bearing marks are preserved unless the user explicitly opts into legacy
rules.

## Optional Legacy Rules

These rules still exist for compatibility but are **not** part of the default
V6.0 rule set:

- `combining_decoration`: delete runs of 2 or more combining marks after a base
  character.
- `exotic_combining`: delete rare combining-mark ranges.

Use `--rules` only when you intentionally want a custom rule set:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.jsonl \
  --output output.custom.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --rules html_entity,bidi_control,pua_known_noise,pua_contextual,decorative_symbol,decorative_combining
```

`--rules all` means all default V6.0 rules, not the optional legacy rules.
`--rules all,exotic_combining` means default V6.0 rules plus that opt-in legacy
rule.

## Cleaning Metadata

With `--add-cleaning-meta`, every JSONL record gets:

```json
{
  "meta": {
    "cleaning_v6": {
      "version": "v6.0",
      "schema": "compact_by_rule_v1",
      "line_no": 1,
      "changed": true,
      "fields": ["content"],
      "changed_fields": ["content"],
      "change_count": 3,
      "rule_counts": {
        "html_entity": 1,
        "decorative_symbol": 1,
        "bidi_control": 1
      }
    }
  }
}
```

Span offsets are Unicode character offsets in the original field value:
`start` is inclusive and `end` is exclusive.

Metadata detail modes:

- `--meta-detail spans`: default and recommended for production.
- `--meta-detail chars`: stores character-level changes.
- `--meta-detail both`: stores both grouped spans and character-level changes.

If an input record already has a dict field named `meta`, cleaning metadata is
added under `meta.cleaning_v6`. If `meta` exists but is not a dict, metadata is
written to `_cleaning_v6` to avoid overwriting source data.

## Standalone QC

Run all QC checks on a cleaned JSONL:

```bash
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input output.v6.cleaned.jsonl \
  --output-dir qc_output \
  --workers 16 \
  --executor process \
  --no-passed-output \
  --failed-records-output qc_output/failed_records.jsonl
```

QC does not clean or delete records. It only reports issues and highlights.

## XLSX Cleaning

Clean a sheet by header names:

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.xlsx \
  --output output.v6.cleaned.xlsx \
  --format xlsx \
  --fields variation_context \
  --sheet Sheet1 \
  --summary output.v6.clean_summary.json
```

For XLSX mode, `--fields` refers to column headers.

## Notes

- The cleaner only modifies fields listed in `--fields`.
- Real newline characters are preserved except excessive blank lines are
  collapsed by `blank_line_collapse`. Standalone literal two-character escape
  strings like `\n` are removed by `forbidden_strings`, while LaTeX commands
  beginning with `\n`, `\t`, or `\r` are preserved when followed by a command
  letter or `{`.
- Tabs are not normalized globally. `line_boundary_space` only deletes spaces or
  tabs sitting directly before a newline or the end of text.
- General continuous duplicate text detected by `text_sub_dedup` is QC-only; it
  is not automatically deleted by the cleaner. The narrow
  `repeated_noise_token` rule is the exception: it removes only an
  alphanumeric token that contains both letters and digits, has an internal
  run of at least four identical characters, and occurs consecutively at least
  five times. Treat that rule as a high-confidence noise pattern, not as a
  general deduplication policy.
