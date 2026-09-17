# 交接包内容与数据边界

## 纳入内容

| 路径 | 用途 |
|---|---|
| `quality_checks_V6/` | 当前可运行的 V6 清洗器、QC、API、测试和依赖清单 |
| `docs/QUALITY_CHECKS_V7_HANDOFF.md` | 短版交接说明 |
| `docs/QUALITY_CHECKS_CASES_AND_OPERATORS.md` | 完整案例和算子手册 |
| `QUALITY_CHECKS_V7_CODEX_PROMPT.txt` | 给 Codex 的启动指令 |
| `PACKAGE_MANIFEST.json` | 机器可读的版本、入口、数据边界和验收信息 |
| `verify_handoff.py` | 不依赖第三方库的目录、语法和合成清洗自检 |
| `SHA256SUMS.txt` | 包内文件 SHA-256 校验值，打包前生成 |
| `clean_article_comment.py` | 独立的文章标题/评论引用前置脚本 |
| `validation/` | 不含正文的 800 条 V6 基线统计 |
| `examples/` | 人工构造的 smoke-test 输入和预期说明 |
| `legacy_notes/` | V5 原理、历史算子目录和历史复查记录 |

## 明确排除

- 原始 `*.jsonl`、`*.xlsx`、`*.xls`、`*.docx` 数据文件；
- 清洗后的大体积 JSONL；
- `issues.jsonl`、`failed_records.jsonl` 及包含正文上下文的 QC 输出；
- 旧版本 ZIP 和重复的构建产物；
- `__pycache__/`、`*.pyc`、`.DS_Store`；
- `/Users/...`、`/root/...` 等本机数据路径对应的实际数据；
- 密钥、令牌、环境变量和全局配置文件。

## 分享级别

这是已移除内部插件的可分享版本，主 V6 代码和文档可以独立运行。

包内脚本不再写入任何固定的本机数据路径。批量 QC 的 `--input` 和文章评论脚本的
`--src` 都必须由接收方显式提供；输出路径也应指向包外的工作目录。

## 接收人验收

解压后应看到本文件、`README.md` 和 `quality_checks_V6/`。先执行 README 中的
单元测试和合成样例，再把自己的数据放到包外部目录运行，避免把数据写回包内。
