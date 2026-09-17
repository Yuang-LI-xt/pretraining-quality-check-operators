# Quality Checks 交接包（面向 V7，当前基线 V6.0）

用于 pretraining 数据质量检查、清洗和重复检测的算子集合。当前可运行基线为 V6.0，仓库同时保留了继续开发 V7 所需的交接文档与验证记录。

打包日期：2026-08-30

这是给下一位维护者和 Codex 使用的内部交接包。包内可直接运行的代码是
`quality_checks_V6`，不是已经完成的 V7；`continuous_dedup_mapper_v7.py`
中的 `v7` 只是历史文件名。V7 需要在 V6 基线上单独建立和验证。

## 先读什么

1. `docs/QUALITY_CHECKS_V7_HANDOFF.md`：短版背景、接手顺序和 V7 任务。
2. `docs/QUALITY_CHECKS_CASES_AND_OPERATORS.md`：完整案例、阈值和算子说明。
3. `QUALITY_CHECKS_V7_CODEX_PROMPT.txt`：可以直接交给 Codex 的启动指令。
4. `quality_checks_V6/README.md`：代码级使用说明。
5. `validation/V6_800_BASELINE.md`：800 条样本的可复核统计。

## 快速验证

在解压后的包根目录执行：

```bash
python3 verify_handoff.py

python3 -m unittest quality_checks_V6.tests.test_repeated_noise_token

python3 - <<'PY'
from pathlib import Path

paths = [
    Path("quality_checks_V6/clean_variation_chars.py"),
    Path("quality_checks_V6/quality_api.py"),
    Path("quality_checks_V6/run_long_dialog_quality_checks.py"),
    Path("quality_checks_V6/mojibake_detect/detector.py"),
    Path("quality_checks_V6/repetition_check/loop.py"),
    Path("quality_checks_V6/text_sub_dedup/continuous_dedup_mapper_v7.py"),
]
for path in paths:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("syntax OK:", len(paths), "files")
PY
```

运行合成样例：

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input examples/demo_input.jsonl \
  --output /tmp/quality_checks_demo.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /tmp/quality_checks_demo.summary.json
```

## 正式使用

校验包内容（在包根目录执行）：

```bash
shasum -a 256 -c SHA256SUMS.txt
```

安装依赖：

```bash
python3 -m pip install -r quality_checks_V6/requirements.txt
```

清洗 JSONL：

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.jsonl \
  --output /path/to/output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --max-in-flight 32 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /path/to/output.v6.summary.json
```

清洗后跑 QC：

```bash
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input /path/to/output.v6.cleaned.jsonl \
  --output-dir /path/to/qc.after \
  --checks all \
  --workers 2 \
  --executor thread \
  --no-passed-output \
  --failed-records-output /path/to/qc.after/failed_records.jsonl
```

XLSX 清洗时，把 `--fields` 换成实际表头，例如
`variation_context`；它按表头名称工作，不按 Excel 列号工作。

## API

Python：

```python
from quality_checks_V6 import clean_record, check_record, clean_and_check
```

HTTP 服务：

```bash
python3 quality_checks_V6/quality_api.py --host 127.0.0.1 --port 8000
```

端点为 `/health`、`/v1/rules`、`/v1/clean`、`/v1/check` 和
`/v1/clean-and-check`。服务没有认证和 TLS，不要直接暴露到公网；大文件优先
使用 CLI。

## 包内结构

```text
quality_checks_handoff_v7_20260830/
├── README.md
├── PACKAGE_CONTENTS.md
├── PACKAGE_MANIFEST.json
├── SHA256SUMS.txt
├── QUALITY_CHECKS_V7_CODEX_PROMPT.txt
├── verify_handoff.py
├── clean_article_comment.py
├── quality_checks_V6/
├── docs/
├── validation/
├── examples/
├── legacy_notes/
└── optional_internal_plugins/
```

## 重要边界

- 不删除整条 JSONL 记录；只对指定字段做高置信度局部修改。
- `meta.cleaning_v6` 保存清洗溯源；offset 是原始 Python Unicode 字符索引。
- 通用 dedup、复杂 mojibake、未知 PUA 和合法重复默认只报告，不自动改正文。
- 不按 Unicode `Mn/Mc/Me` 类别粗删，不全量删除 PUA。
- 原始输入路径必须和输出路径不同。
- `hex_blob` 的 64 字符规则仍可能误命中合法 hash、ID 或 URL，V7 需要优先补上下文保护。

## 数据和内部依赖

本包没有包含原始 JSONL/XLSX、清洗后的大文件、QC 上下文明细、旧压缩包、
`__pycache__` 或 `*.pyc`。`optional_internal_plugins/` 下的两个插件依赖公司内部
`aegis_data` 环境，不属于 V6 的默认 CLI/API 链路；如果包要发到组织外部，建议
先删除该目录。完整纳入/排除说明见 `PACKAGE_CONTENTS.md`。

## V7 开发起点

先冻结并重跑 V6 基线，再建立独立的 `quality_checks_V7` 目录。优先级建议是：

1. 修复 repetition 可视化的绝对 offset，停止从 reason 字符串反查位置；
2. 为 `hex_blob` 增加 hash、URL、代码和 ID 的上下文保护；
3. 建立 strict/language-safe 等模式，重新评估 emoji、ZWJ/ZWNJ；
4. 降低教材、歌词、表格和课堂指令的 dedup 误报；
5. 仅在 before/after、误删样例和性能数据齐全后更新默认规则。

交接时应把整个 ZIP 和同目录的 `.sha256` 文件一起交给下一位维护者；
`QUALITY_CHECKS_V7_CODEX_PROMPT.txt` 已包含在 ZIP 内。让对方在包根目录打开
Codex 任务并先运行上面的验证命令。
