# Pretraining Quality Check Operators

面向大模型预训练数据的文本质量检查与清洗算子集合。项目提供可审计的局部清洗、质量检测、批量处理和 HTTP API，适用于 JSONL、XLSX 及 Python 数据处理流程。

当前稳定实现位于 `quality_checks_V6/`，版本为 `v6.0`。

## 主要能力

- 清理异常 Unicode 字符、不可见控制字符、HTML 残留和训练特殊 token；
- 规范异常空格、换行、HTML 实体和数学样式字符；
- 检测乱码、连续重复、token flood 和疑似重复片段；
- 对高置信度噪声执行局部修改，不默认删除整条记录；
- 在 `meta.cleaning_v6` 中记录规则、计数和原文位置，便于审计；
- 支持单进程和多进程批量处理、Python API 及 HTTP API。

通用 dedup、复杂 mojibake、未知 PUA 和可能合法的重复内容默认只报告，不自动修改正文。

## 快速开始

安装依赖：

```bash
python3 -m pip install -r quality_checks_V6/requirements.txt
```

运行自检：

```bash
python3 verify_handoff.py
python3 -m unittest quality_checks_V6.tests.test_repeated_noise_token
```

使用仓库内的合成样例：

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

## 批量清洗

```bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.jsonl \
  --output /path/to/output.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --max-in-flight 32 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /path/to/output.summary.json
```

输入与输出必须使用不同路径。处理 JSONL 时保留记录数量，只修改指定字段。XLSX 输入同样受支持，`--fields` 应填写实际表头名称，而不是 Excel 列号。

## 质量检查

```bash
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input /path/to/output.cleaned.jsonl \
  --output-dir /path/to/qc-output \
  --checks all \
  --workers 2 \
  --executor thread \
  --no-passed-output \
  --failed-records-output /path/to/qc-output/failed_records.jsonl
```

质量检查会输出汇总和问题上下文，便于抽样复核。包含真实正文的 QC 输出不应提交到代码仓库。

## Python API

```python
from quality_checks_V6 import check_record, clean_and_check, clean_record

record = {"content": "example text"}
cleaned = clean_record(record, fields=("content",))
quality = check_record(cleaned["record"])
result = clean_and_check(record, fields=("content",))
```

更完整的参数和返回结构见 [`quality_checks_V6/README.md`](quality_checks_V6/README.md)。

## HTTP API

```bash
python3 quality_checks_V6/quality_api.py --host 127.0.0.1 --port 8000
```

可用端点：`GET /health`、`GET /v1/rules`、`POST /v1/clean`、`POST /v1/check` 和 `POST /v1/clean-and-check`。

服务本身不提供认证或 TLS，请勿直接暴露到公网。大文件处理优先使用 CLI。

## 核心规则

默认规则覆盖 variation selector、数学样式字符、异常空格、双向控制符、已知 PUA 噪声、装饰字符、emoji、不可见控制字符、HTML 实体、训练特殊 token、重复噪声 token、长十六进制串、符号洪泛以及换行规范化。

规则定义、阈值和案例见 [`quality_checks_V6/OPERATORS.md`](quality_checks_V6/OPERATORS.md) 与 [`docs/QUALITY_CHECKS_CASES_AND_OPERATORS.md`](docs/QUALITY_CHECKS_CASES_AND_OPERATORS.md)。

## 项目结构

```text
.
├── quality_checks_V6/   # 清洗器、质量检查、API 和测试
├── examples/            # 合成示例数据
├── validation/          # 不含原文的基线统计
├── docs/                # 规则案例与设计说明
├── legacy_notes/        # 历史设计记录
├── clean_article_comment.py
└── verify_handoff.py
```

## 安全边界

- 不覆盖原始输入文件；
- 不默认删除完整 JSONL 记录；
- 不按 Unicode 类别粗暴删除全部组合字符或 PUA；
- 对合法 hash、ID、URL、代码、表格和多语种文本进行人工抽样复核；
- 不在仓库中提交原始业务数据、清洗后的大文件或带正文的 QC 明细。

## 验证结果

仓库包含 800 条样本的 V6 基线统计。清洗前后均保持 800 条记录，详细数据见 [`validation/V6_800_BASELINE.md`](validation/V6_800_BASELINE.md)。

可使用以下命令校验仓库文件：

```bash
shasum -a 256 -c SHA256SUMS.txt
```
