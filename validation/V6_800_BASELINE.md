# V6.0 800 条基线摘要

运行日期：2026-08-25

本目录只保留统计，不包含原始文本、清洗后的 JSONL 或 QC 上下文明细。

## 配置

- 输入：`sample_100_per_domain.jsonl`，800 条
- 清洗字段：`content`
- 清洗版本：V6.0 默认规则
- 元信息：`--add-cleaning-meta --meta-detail spans`
- QC：`mojibake,repetition,dedup`
- QC 执行器：`thread`，2 workers

## 结果

| 阶段 | 通过 | 失败 | 主要原因 | 用时 |
|---|---:|---:|---|---:|
| 原始数据 | 422 | 378 | mojibake 367，连续重复 16 | 348.280 s |
| V6 清洗后 | 783 | 17 | mojibake 1，连续重复 16 | 345.423 s |

清洗阶段没有删除记录：800 条输入对应 800 条输出；537 条记录的 `content`
发生变化，变化单元合计 45,526。

## 清洗统计

| 规则 | 变化单元数 |
|---|---:|
| `control_invisible` | 13,554 |
| `line_boundary_space` | 9,618 |
| `blank_line_collapse` | 8,033 |
| `abnormal_space` | 5,546 |
| `decorative_symbol` | 4,261 |
| `emoji_all` | 1,880 |
| `variation_selector` | 1,011 |
| `hex_blob` | 480 |
| `html_entity` | 264 |
| `styled_math_alnum` | 490 |
| `math_symbol_flood` | 375 |
| `line_separator` | 7 |
| `pua_contextual` | 6 |
| `bidi_control` | 1 |

## 残留解释

清洗后 16 条连续重复主要是教材、课堂指令、枚举、歌词/文学表达和拟声词，
不能仅凭重复次数自动删除。剩余 1 条 mojibake 是英文文本中的三个 C1 控制字符
（U+0091/U+0093/U+0094）；V6 只报告它们，没有猜测原始标点。

## 性能口径

该次 QC 约为 2.30-2.32 records/s，按完整 JSONL 文件大小折算约 185-187 KB/s。
这是当时机器、数据和配置的实测值，不是固定算法保证。新环境应重新记录
records/s、UTF-8 bytes/s 和内存峰值。
