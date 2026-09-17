# Quality Checks V5 函数与算子原理说明

本文档用于说明 `quality_checks_V5` 中每个核心函数的作用、判断原理和所在链路。目标是让交接同学不用逐行读源码，也能知道每个算子在做什么、为什么这样做、输出结果怎么理解。

对应代码目录：

- `quality_checks_V5/clean_variation_chars.py`：字符清洗主链路
- `quality_checks_V5/run_long_dialog_quality_checks.py`：清洗前/后质量检测入口
- `quality_checks_V5/mojibake_detect/`：乱码与异常字符检测
- `quality_checks_V5/repetition_check/`：生成式重复检测
- `quality_checks_V5/text_sub_dedup/`：连续子串重复检测

---

## 一、整体链路

V5 不是单一规则，而是两条链路：

| 链路 | 入口函数 | 作用 |
|---|---|---|
| 清洗链路 | `clean_text` / `clean_jsonl` / `clean_xlsx` | 真正修改文本，删除或替换异常字符，并可写入 `meta.cleaning_v5` |
| QC 检测链路 | `run_long_dialog_quality_checks.py::main` | 不直接修改文本，只发现可疑问题，输出 `issues.jsonl` 和 `summary.json` |

核心设计：

1. 清洗函数只处理高置信字符级噪声，例如异常空格、HTML 实体、装饰符号、私用区已知噪声等。
2. 检测函数负责发现更复杂的问题，例如乱码片段、重复回答、连续子串刷屏。
3. 默认规则尽量“保语义”：不全量删除 PUA，不按 Unicode `Mn/Me` 类别粗删组合符，不误伤 LaTeX 命令。

---

## 二、清洗算子总览

这些算子由 `clean_variation_chars.py::clean_text` 统一调度。

| 算子 | 处理对象 | 动作 | 原理 |
|---|---|---|---|
| `variation_selector` | Unicode 字形选择符 | 删除 selector 本身 | `U+FE00-U+FE0F`、`U+E0100-U+E01EF` 只影响字形，不保留训练价值 |
| `styled_math_alnum` | 数学样式字母数字 | NFKC 规范化 | `𝔽 -> F`，尽量保留可读语义 |
| `abnormal_space` | 异常空格 | 替换为空格或删除 | `NBSP/EM SPACE/全角空格` 归一，窄空格/细空格删除 |
| `bidi_control` | 双向文本控制符 | 删除 | 删除显示方向控制字符，避免文本顺序被隐藏改变 |
| `pua_known_noise` | 已知 PUA 噪声 | 删除 | 只删 `U+F8FF` Apple logo、`U+F04A` 字体私有笑脸 |
| `pua_contextual` | 部分 PUA 字符 | 按上下文修复/删除 | 只在高置信语境修复 bullet、范围符、脚注等 |
| `decorative_symbol` | 装饰符号 | 删除 | 删除 box/block/geometric/misc/dingbat 范围，但保留表单符号 |
| `decorative_combining` | 装饰组合符白名单 | 删除 | 只删 `U+0336/U+033F/U+035C-U+0361`，避免损坏多语种 |
| `emoji_all` | emoji 与附件字符 | 删除 | 删除 emoji 本体、ZWJ、keycap、enclosing mark 等 |
| `control_invisible` | 不可见控制字符 | 删除 | 删除 BOM、零宽字符、soft hyphen 等正文污染 |
| `line_separator` | Unicode 行/段分隔符 | 替换 | `U+2028 -> \n`，`U+2029 -> \n\n` |
| `html_entity` | HTML 实体 | 删除整串 | 匹配 named/decimal/hex entity，例如 `&amp;`、`&#123;` |
| `forbidden_strings` | 禁止字面串 | 删除 | 删除 HTML 残留、训练 token、独立字面量 `\n/\t/\r` |
| `math_symbol_flood` | 重复符号洪泛 | 删除整段 | 连续相同数学/符号字符达到阈值后删除，例如 `====` |
| `line_boundary_space` | 行尾/文本尾空格 | 删除 | 在前序替换结果基础上删除行边界空格 |
| `blank_line_collapse` | 过多连续换行 | 压缩 | 连续换行超过 2 个时删除多余换行 |
| `combining_decoration` | legacy 可选规则 | 删除组合符 run | 仅手动启用，base 后连续多个 combining mark 才删 |
| `exotic_combining` | legacy 可选规则 | 删除 | 罕见组合符块，默认不启用 |

---

## 三、`clean_variation_chars.py` 函数说明

### 1. 字符分类与单字符规则

| 函数 | 作用 | 原理 |
|---|---|---|
| `_in_ranges(cp, ranges)` | 判断码点是否落在某些 Unicode 范围内 | 遍历 `(start, end)` 区间，命中即返回 `True` |
| `classify_char(ch)` | 单字符规则分类入口 | 按规则优先级返回 `(rule, replacement)`，例如 selector 删除、数学样式字母 NFKC、异常空格替换 |
| `is_emoji_variation_base(ch)` | 判断字符是否可作为 emoji variation sequence 的 base | 匹配 emoji variation base 范围和 `#*0123456789©®` 等特殊 base |
| `_previous_non_replaced_base(chars, idx, replacements)` | 找 keycap/emoji 序列前面的 base 字符 | 如果前一个字符是 variation selector，会再往前跳一位 |
| `char_info(ch, rule, replacement)` | 生成单字符清洗说明 | 输出字符、unicode escape、码点、Unicode 名称、类别、规则、替换值 |

典型例子：

```text
𝔽 -> F
U+FE0F -> 删除
U+202F -> 删除
U+2003 -> 普通空格
```

### 2. 组合符与 PUA 上下文扫描

| 函数 | 作用 | 原理 |
|---|---|---|
| `_is_combining_mark(ch)` | 判断是否为组合符 | 使用 Unicode category `Mn/Mc/Me`，但排除 variation selector |
| `_scan_combining_decoration(chars, threshold)` | legacy 装饰组合符 run 检测 | base 后连续组合符数量达到阈值才删除组合符，base 保留 |
| `_is_line_start_after_indent(chars, idx)` | 判断某位置是否在行首缩进之后 | 向前跳过空格/Tab/NBSP/全角空格，若到文本开头或换行后则为行首 |
| `_next_nonspace_char(chars, idx)` | 找当前位置后的第一个非空白字符 | 用于判断 PUA bullet 后面是否有正文 |
| `_prev_window(chars, idx, size)` | 取当前位置前的小窗口 | 给上下文正则使用 |
| `_next_window(chars, idx, size)` | 取当前位置后的小窗口 | 给上下文正则使用 |
| `_scan_pua_contextual(chars)` | PUA 高置信修复核心 | 只对明确上下文中的 PUA 做替换/删除，例如列表 bullet、数字范围、脚注、私有码点点号 |

`_scan_pua_contextual` 的典型逻辑：

| 字符 | 条件 | 处理 |
|---|---|---|
| `U+F0B7` | 行首列表项 | `-` 或 `- ` |
| `U+F07E` | 左右是数字/单位范围 | `~` |
| `U+E010` | 左右像编号/正文点号 | `．` |
| `U+E011` | 英文名中间 | `-` |
| `U+E10B` | 英文名中间 | `'` |
| `U+E11A/E11B/E11C` | ebook 脚注标记 | 删除 |
| `U+E618` | bullet | `-` 或换行后 `-` |
| `U+E1BD` | 后面是 `束` | `约` |

### 3. 多字符 span 规则

| 函数 | 作用 | 原理 |
|---|---|---|
| `find_forbidden_string_spans(text)` | 找禁止字面串 | 删除 HTML 残留、训练 token、控制字符、独立字面量 `\n/\t/\r` |
| `find_html_entity_spans(text)` | 找 HTML 实体 | 正则匹配 `&name;`、`&#123;`、`&#xABCD;` |
| `_is_math_flood_symbol(ch)` | 判断是否是符号洪泛候选字符 | Unicode 类别为 `Sm/So` 或在额外集合 `=+~×═` 中 |
| `_scan_math_symbol_flood(chars, threshold)` | 找连续相同符号洪泛 | 同一个符号连续出现达到阈值后，整段删除 |

`forbidden_strings` 特别注意：

```text
\\n\\t -> 删除
\\times -> 保留
\\theta -> 保留
```

原因是当前正则使用 `\\{1,2}[ntr](?![A-Za-z{])`，要求 `\n/\t/\r` 后面不能紧跟字母或 `{`，避免误删 LaTeX 命令。

### 4. 后处理规则：行边界空格与空行压缩

| 函数 | 作用 | 原理 |
|---|---|---|
| `_effective_replacement_char(chars, replacements, idx)` | 取某字符清洗后的有效输出 | 如果该位置已有 replacement，就用 replacement，否则用原字符 |
| `_iter_effective_units(chars, replacements)` | 遍历清洗后的输出单元 | 支持一个原字符替换成多个输出字符，例如 `U+2029 -> \n\n` |
| `_mark_unit(unit_deletions, idx, pos)` | 标记某个输出单元需要删除 | 用于删除 replacement 内部的某一位 |
| `_apply_unit_deletions(chars, replacements, unit_deletions, new_rule, reason)` | 将输出单元级删除合并回 replacements | 支持在前序替换结果基础上继续删除 |
| `_scan_line_boundary_space(chars, replacements)` | 找行尾/文本尾空格和 Tab | 基于“前序清洗后的有效文本”判断，不只看原文 |
| `_scan_blank_line_collapse(chars, replacements, max_newlines=2)` | 找连续多余换行 | 连续 `\n` 超过 `2` 后标记多余部分删除 |

这两个算子放在后面执行，是因为它们要看前面规则替换后的结果。例如 `U+2029` 先变成两个换行，然后 `blank_line_collapse` 再判断是否需要压缩。

### 5. 清洗主函数与文件处理

| 函数 | 作用 | 原理 |
|---|---|---|
| `clean_text(text, rules=DEFAULT_RULES)` | 单条文本清洗核心入口 | 先逐字符分类，再执行 PUA、多字符 span、符号洪泛、行边界空格、空行压缩，最后生成清洗文本和 change list |
| `_parse_rules(value)` | 解析 CLI `--rules` | 支持默认规则、显式规则、`all` 加 legacy 规则 |
| `_clean_jsonl_line(task)` | 清洗一行 JSONL | 解析 JSON，清洗指定字段，统计规则命中，可写入 `meta.cleaning_v5` |
| `clean_jsonl(...)` | JSONL 文件级清洗入口 | 支持单进程/多进程；多进程时保证输出行顺序不变 |
| `_load_openpyxl()` | 延迟加载 `openpyxl` | 只有清洗 XLSX 时才要求安装依赖 |
| `clean_xlsx(...)` | XLSX 文件级清洗入口 | 按表头字段清洗单元格，支持 dry-run |
| `_infer_format(path, explicit)` | 推断输入格式 | 根据扩展名判断 `jsonl` 或 `xlsx` |
| `_default_output_path(input_path, fmt)` | 生成默认输出路径 | 默认写成 `<input>.cleaned.<ext>` |
| `_assert_output_not_input(input_path, output_path, dry_run)` | 防止覆盖源文件 | 非 dry-run 时禁止 input 和 output 是同一路径 |
| `_write_summary(summary, summary_path)` | 写 summary JSON | 创建父目录并写入清洗统计 |
| `_run_optional_qc(...)` | 清洗命令里可选调用 QC | 通过子进程执行 `run_long_dialog_quality_checks.py` |
| `build_arg_parser()` | 定义清洗 CLI 参数 | 包括 input/output/fields/rules/meta/workers/qc 等 |
| `main()` | 清洗 CLI 总入口 | 解析参数，执行清洗，可选执行 before/after QC，输出 summary |

### 6. `meta.cleaning_v5` 构造函数

| 函数 | 作用 | 原理 |
|---|---|---|
| `_meta_replacement_value(value)` | 将 legacy replacement repr 转成真实文本 | 用 `ast.literal_eval` 解析类似 `''`、`' '` 的字符串 |
| `_meta_text_preview(value, limit)` | 生成 meta 中的短预览 | 太长时保留头尾，中间用 `...` |
| `_compact_span(span_items)` | 把连续字符 change 合成 span | 生成 start/end/count/action/original_preview/replacement_preview/codepoint_counts |
| `_build_spans_by_rule(row_removed, max_meta_spans)` | 按规则聚合 span | 同一 rule、field、连续 offset 合并；支持 span 截断 |
| `_build_char_changes(row_removed, max_meta_changes)` | 生成字符级 changes | 按上限截断，避免 meta 过大 |
| `_build_cleaning_meta(...)` | 构建单条记录的完整 cleaning meta | 写入 version/schema/rule_counts/field_counts/by_rule/changes |
| `_attach_cleaning_meta(record, cleaning_meta)` | 将 meta 写回 JSON record | 如果原 `meta` 是 dict，写到 `meta.cleaning_v5`；否则写 fallback key |

`meta.cleaning_v5` 的价值是可追溯：

```json
{
  "version": "v5.0",
  "schema": "compact_by_rule_v1",
  "rule_counts": {"abnormal_space": 3},
  "by_rule": {
    "abnormal_space": {
      "count": 3,
      "spans": [...]
    }
  }
}
```

---

## 四、`run_long_dialog_quality_checks.py` 函数说明

这个文件是 QC 总入口，不负责清洗，只负责发现问题并输出报告。

### 1. 配置、取文本与语言判断

| 函数 | 作用 | 原理 |
|---|---|---|
| `_parse_checks(value)` | 解析 `--checks` 参数 | 支持 `all/mojibake/repetition/dedup` |
| `_init_worker(config)` | 初始化 worker | 每个进程只 import 一次检测模块，减少重复开销 |
| `_get_text(record)` | 从 JSON record 取正文 | 依次尝试 `content`、`text`、`tgt` |
| `_get_lang(record, text)` | 判断文本语言 | 优先读 `meta.lang/lang`，否则按前 2000 字 CJK 占比判断中英文 |

### 2. 命中片段与字符信息

| 函数 | 作用 | 原理 |
|---|---|---|
| `_text_slice(text, start, end, context_chars)` | 截取命中片段和上下文 | 输出 offset、命中文本、context offset、上下文 |
| `_char_info(text, index)` | 生成某字符的 Unicode 信息 | 输出 repr、unicode escape、码点、名称、类别 |
| `_hit_char_infos(text, base_start, detail, max_chars)` | 从 mojibake hit_positions 还原命中字符 | 把窗口内相对位置转成全文绝对位置 |
| `_summarize_mojibake(result, text)` | 汇总 mojibake 检测结果 | 统计各规则命中数，并生成可人工复核的 highlight |

### 3. 三类 QC 检测封装

| 函数 | 作用 | 原理 |
|---|---|---|
| `_check_mojibake(text)` | 执行乱码检测 | 调用 `MojibakeDetector.exec_text`，生成 issue 字符串和 highlights |
| `_find_repetition_offset(text, reason)` | 给 repetition reason 定位文本位置 | 从 reason 中解析 top ngram 或 token，再在原文里查找 |
| `_check_repetition(text)` | 执行生成式重复检测 | 把正文包装成 assistant message，依次跑 ngram/token_flood/runaway_enum |
| `_check_dedup(text, lang)` | 执行连续子串重复检测 | 调用 `detect_split_content`，输出重复片段 offset 和 repeat_num |

### 4. 行级处理与输出

| 函数 | 作用 | 原理 |
|---|---|---|
| `_issue_type(reason)` | 提取 issue 类型 | 取冒号前缀，例如 `mojibake`、`repetition_ngram` |
| `_process_line(task)` | 处理单条 JSONL | 解析 JSON，取正文，跑启用的 checks，返回 report |
| `_open_optional(path)` | 按需打开输出文件 | 空路径返回 `None`，否则创建父目录并打开文件 |
| `_default_passed_output(input_path)` | 生成 passed records 默认路径 | 默认 `<input>_filtered.jsonl` |
| `_format_duration(seconds)` | 格式化 ETA | 秒数转成 `1h2m3s`、`2m3s` 等 |
| `_format_bytes(num_bytes)` | 格式化字节量 | 转成 B/KB/MB/GB/TB |
| `_handle_result(result, line, writers, stats, reasons)` | 写 issues/passed/failed records 并更新统计 | failed 写 `issues.jsonl`，passed 可写过滤后原始记录 |
| `_build_arg_parser()` | 定义 QC CLI 参数 | 包括 checks/workers/executor/output 等 |
| `main()` | QC CLI 总入口 | 多进程/多线程调度 `_process_line`，写 summary 和 progress |

---

## 五、`mojibake_detect` 函数说明

### 1. `utils.py`：底层字符判定函数

| 函数 | 作用 | 原理 |
|---|---|---|
| `is_fffd(char)` | 检测替换符 | 判断是否为 `U+FFFD` |
| `is_cscn(char)` | 检测代理/未分配字符 | Unicode category 是否为 `Cs/Cn` |
| `is_c1_control(char)` | 检测 C1 控制字符 | 码点是否在 `0x80-0x9F` |
| `is_pua(char)` | 检测 BMP PUA | 码点是否在 `U+E000-U+F8FF` |
| `is_known_pua_noise(char)` | 检测已知 PUA 噪声 | 只命中 `U+F04A/U+F8FF` |
| `is_variation_selector(char)` | 检测 variation selector | `U+FE00-U+FE0F` 或 `U+E0100-U+E01EF` |
| `is_styled_math_alnum(char)` | 检测数学样式字母数字 | `U+1D400-U+1D7FF` |
| `is_exotic_combining(char)` | 检测罕见组合符 | 扩展组合符范围或少量孤立白名单 |
| `is_abnormal_space(char)` | 检测异常空格 | 是否在 `ABNORMAL_SPACES` |
| `is_bidi_control(char)` | 检测双向控制符 | 是否在 bidi 控制符集合 |
| `is_decorative_symbol(char)` | 检测装饰符号 | 命中装饰范围且不在语义表单符号白名单 |
| `is_decorative_combining(char)` | 检测装饰组合符 | 精确命中 V5 白名单组合符 |
| `is_combining_mark(char)` | 检测组合符 | Unicode category `Mn/Mc/Me`，排除 selector |
| `is_math_flood_symbol(char)` | 检测洪泛符号候选 | 类别为 `Sm/So` 或额外符号集合 |
| `is_control_format_char(char)` | 检测格式/分隔控制字符 | Unicode category 为 `Cf/Zl/Zp` |
| `legit_script_ratio(text)` | 计算复杂文字系统占比 | legacy 辅助函数，V5 不用它做整条判废 |
| `has_complex_script(text)` | 判断是否包含复杂文字系统 | legacy 辅助函数，保留兼容 |

### 2. `custom_patterns.py`：正则模式

| 对象 | 作用 | 原理 |
|---|---|---|
| `html_entity` | HTML 实体检测 | named/decimal/hex entity 正则 |
| `garbled_seq_pattern_onehit` | 中文乱码强特征 | 命中一次即可认为可疑，例如 `æ–‡`、`é”™` |
| `garbled_seq_pattern_twohits` | 中文乱码弱特征 | 需要两种以上不同字符命中，降低误报 |
| `double_enc_2byte/3byte/4byte` | 双重编码结构检测 | 匹配 UTF-8 被错误二次编码后的码位模式 |
| `cp1252_mojibake` | CP1252 乱码检测 | 匹配 `Ã/Â/â/ð` 等常见误解码片段 |

### 3. `detector.py::MojibakeDetector`

MojibakeDetector 的核心思想是：每条规则返回一个和文本等长的布尔数组，`True` 表示该位置命中；最后按窗口聚合为可读报告。

| 方法 | 作用 | 原理 |
|---|---|---|
| `__init__(enable_cscn=True, thresholds=None)` | 初始化检测器 | 设置是否启用 Cs/Cn，并允许覆盖阈值 |
| `get_fffd_hit(text)` | 替换符检测 | 标记所有 `U+FFFD` |
| `get_cscn_hit(text)` | Cs/Cn 检测 | 标记代理/未分配码位 |
| `get_pattern_hit(text)` | 中文乱码模式检测 | 强特征直接标记，弱特征需多字符命中 |
| `get_c1_control_hit(text)` | C1 控制符检测 | 标记 `0x80-0x9F` |
| `get_double_enc_hit(text)` | 双重编码检测 | 结构化匹配数达到阈值才标记 |
| `get_cp1252_hit(text)` | CP1252 乱码检测 | 匹配数达到阈值才标记 |
| `get_pua_hit(text)` | PUA 洪泛检测 | PUA 总数达到阈值才标记，避免单个 PUA 误报 |
| `get_pua_known_noise_hit(text)` | 已知 PUA 噪声检测 | 已知坏点零容忍 |
| `get_html_entity_hit(text)` | HTML 实体检测 | 标记整个 entity span |
| `get_bidi_control_hit(text)` | 双向控制符检测 | 标记 bidi 控制字符 |
| `get_decorative_symbol_hit(text)` | 装饰符号检测 | 标记装饰符号，表单符号白名单不命中 |
| `get_decorative_combining_hit(text)` | 装饰组合符检测 | 标记 V5 精确白名单组合符 |
| `get_zw_flood_hit(text)` | 零宽字符洪泛检测 | 零宽字符达到阈值后标记 |
| `get_invis_garbage_hit(text)` | 不可见垃圾字符检测 | BOM/WORD JOINER/SOFT HYPHEN 等达到阈值后标记 |
| `get_specials_hit(text)` | Specials 区段检测 | 标记 `U+FFF0-U+FFFC` 等特殊垃圾字符 |
| `get_conditional_zw_hit(text)` | 条件性零宽字符检测 | `U+200B/U+200C/U+200D/U+200E/U+200F` 按阈值标记 |
| `get_variation_selector_hit(text)` | selector 检测 | 标记 variation selector |
| `get_styled_math_alnum_hit(text)` | 数学样式字母检测 | 标记 `U+1D400-U+1D7FF` |
| `get_abnormal_space_hit(text)` | 异常空格检测 | 标记 V5 abnormal spaces |
| `get_math_symbol_flood_hit(text)` | 符号洪泛检测 | 同一符号连续重复达到阈值才标记 |
| `get_control_format_flood_hit(text)` | 格式控制字符洪泛检测 | `Cf/Zl/Zp` 总数达到阈值才标记 |
| `reorganize(window_result, text_len)` | 聚合窗口结果 | 计算每条规则的命中数、命中率、位置字符串 |
| `get_old_rule_hits(text)` | 旧规则集合 | 执行 `fffd/seq/cscn` |
| `get_new_rule_hits(text)` | 新规则集合 | 对全文执行 V5 新增检测规则 |
| `split_content(content)` | 按换行切窗口 | 每个段落/行作为一个窗口，保留换行 |
| `exec_text(text)` | 检测入口 | 全文跑新规则，窗口跑旧规则，再合并输出 hits |

---

## 六、`repetition_check/loop.py` 函数说明

这一模块检测模型输出中常见的重复、刷屏、空枚举。

| 函数 | 作用 | 原理 |
|---|---|---|
| `_is_punct_token(tok)` | 判断 token 是否全是中文/全角标点 | 全标点 token 在 ngram 检测中忽略 |
| `_tokenize(text)` | 混合中英文分词 | CJK 按字切，非 CJK 按空白切，去掉独立中文标点 |
| `_ngram_repetition_score(text, n, threshold)` | 计算 ngram 重复比例 | 统计所有 ngram，重复 ngram 占比超过阈值则返回 top ngram |
| `_runaway_counter_score(text)` | 检测递增数字枚举导致的重复 | 找连续数字序列，把数字位置归一为空格后再跑 ngram 重复 |
| `check_repetition_ngram(record)` | 检测 assistant 内容 ngram 重复 | 先去掉 `<think>` 前缀，再跑 runaway、10gram、5gram |
| `check_token_flood(record)` | 检测单 token 洪泛 | top token 占比超过阈值即报错 |
| `check_runaway_enum(record)` | 检测空枚举刷屏 | 连续很多行只有编号，或编号行比例过高 |

典型 case：

```text
1.
2.
3.
...
```

```text
hello hello hello hello hello ...
```

---

## 七、`text_sub_dedup` 函数说明

这一模块用于发现“连续重复子串”，例如一段话、一个短语、一个中文片段连续重复 5 次以上。

### 1. `continuous_dedup_mapper_v7.py`

| 函数 | 作用 | 原理 |
|---|---|---|
| `extract_repeated_phrases(text, min_short_length, min_long_length)` | 用正则找重复片段候选 | 短片段要求至少三连，长片段要求两连 |
| `get_repeat_num_and_end(content, offset, repeat_text)` | 计算某片段连续重复次数和结束位置 | 从 offset 开始逐段比较，兼容末尾空格差异 |
| `init_bitmap(length)` | 初始化覆盖标记 | 用于避免重复上报同一重复区域 |
| `set_bitmap(bitmap, start, end, val)` | 标记区间 | 将某段位置设置为已覆盖 |
| `check_bitmap_strict(bitmap, start, end)` | 判断区间是否已完全覆盖 | 完全覆盖则跳过，避免重复 case |
| `find_repeated_phrases(text, min_short_length, min_long_length)` | 查找并去重重复候选 | 先短片段后长片段，用 bitmap 去重 |
| `check_cn_standard(target_len, repeat_num)` | 中文重复阈值判断 | 目标长度大于 0 且重复次数 >= 5 |
| `check_en_standard_char(target_len, repeat_num)` | 英文字符级重复阈值 | 当前返回 `False`，英文主要走 word 级 |
| `check_en_standard_word(target_len, repeat_num)` | 英文词级重复阈值 | 目标词数大于 0 且重复次数 >= 5 |
| `keep_chinese_characters(text)` | 只保留中文字符 | 用于计算中文有效长度 |
| `keep_english_characters(text)` | 只保留英文字母 | 用于计算英文有效长度 |
| `is_paragraph_start(text, offset)` | 判断 offset 是否在段首 | 前面是文本开头、换行，或换行后的缩进空格 |
| `get_next_nonspace_offset(text, offset)` | 跳过空白找下一个位置 | 用于段落位置判断 |
| `detect_continuous_repetition(data)` | 中文/非英文连续重复检测入口 | 去标点空白后快筛，再定位原文 offset，过滤代码/表格/部分正常格式 |
| `filter_latex_str(text)` | 过滤部分 LaTeX 命令 | 避免 `\cdots`、`\underline` 干扰英文重复判断 |
| `detect_continuous_repetition_en(data)` | 英文连续重复检测入口 | 按词级重复判断，过滤代码、歌词、分隔线、LaTeX/HTML/模板符号 |
| `split_long_str(long_str, size, overlap)` | 超长文本切片 | 按长度切片并保留 overlap，避免超长文本正则开销太大 |
| `get_value_from_ranges(value, value_ranges)` | 根据文本长度选择切片参数 | 长文本用更小切片和更小 overlap |
| `get_new_pos(offset, src_len, is_start)` | src/tgt 拼接场景 offset 转换 | legacy 辅助函数 |
| `convert_start_and_end(is_content, src_len, tgt_len, start, end)` | 将拼接 offset 转回字段位置 | legacy 辅助函数，当前 content 场景主要直接返回 content offset |
| `detect_split_content(data, language)` | 对外检测入口 | 按长度切片，分别检测，再把局部 offset 加回全文 offset |
| `need_detect_continuous_char(data, short_sizes, long_sizes, single_repeat_num, lang)` | 字符级快筛 | 快速判断是否可能存在连续重复，减少正则重检测成本 |
| `need_detect_continuous_word(data, short_sizes, long_sizes, single_repeat_num, lang)` | 词级快筛 | 英文按 split 后的 word 序列检测重复可能性 |
| `need_detect_continuous_en(content, size)` | legacy 英文快筛 | 返回可能重复的英文片段集合，当前主链路基本不用 |

### 2. `text_utils.py`

| 函数 | 作用 | 原理 |
|---|---|---|
| `replace_unicode_punct(text)` | Unicode 标点替换为 ASCII/普通形式 | 按 `UNICODE_PUNCT` 映射替换 |
| `remove_unicode_punct(text)` | 删除 Unicode 标点 | 正则删除映射表里的 Unicode 标点 |
| `remove_split_punct(text)` | 删除句子切分标点 | 删除 `,.?!;。！？` 等 |
| `strip_accents(line)` | 去除重音符 | NFD 分解后删除 `Mn` |
| `remove_non_printing_char(text)` | 删除控制字符 | 删除 `0-31` 和 `127-159` |
| `normalize_spacing_for_tok(text, language)` | tokenization 前空格/标点规范化 | 修复括号、百分号、引号、数字小数点等空格 |
| `normalize(line, accent, case, numbers, punct)` | 通用文本归一化 | 小写、去重音、数字归零、标点替换/删除、去控制字符 |
| `slow_normalize_for_dedup(line)` | dedup 归一化慢版本 | 调用 `normalize`，保留更通用逻辑 |
| `normalize_for_dedup(line)` | dedup 归一化快版本 | 小写、数字归零、删除标点/控制字符、压缩空白 |
| `remove_punctuation(sentence)` | 删除句尾标点 | 用于不保留 delimiter 的句子切分 |
| `split_mix_paragraph(text, keep_delimiter)` | 多语言句子切分 | 保护英文缩写和小数点，再按句末标点切分 |
| `remove_unprintable(in_strs)` | 删除不可打印字符 | 使用 Python `isprintable()` |
| `in_stream(encoding, errors)` | 标准输入逐行读取 | Hadoop/streaming 风格输入工具 |
| `get_offset_map(org_text, text)` | 建立清洗后文本到原文 offset 的映射 | 顺序扫描原文，记录新 offset 对应原 offset |
| `remove_punctuation_and_whitespace(text)` | 删除中英文标点和所有空白 | 中文重复检测前的简化文本 |
| `has_cn_char(text)` | 判断是否含中文 | 检查 `U+4E00-U+9FFF` |
| `has_en_char(text)` | 判断是否含英文 | 检查 ASCII 字母 |
| `has_target_char(text, lang)` | 判断是否含目标语言字符 | `en` 看英文，`ch` 看中文，其他看中英任一 |
| `is_code(data)` | 判断文本是否像代码 | 命中 `code::` 或 fenced code block 则跳过重复检测 |

---

## 八、读代码时最重要的几个原则

1. 清洗规则是有优先级的，`clean_text` 先做单字符分类，再做上下文 span，再做后处理。
2. V5 的 PUA 策略是“已知噪声删除 + 高置信上下文修复”，不是全范围删除。
3. V5 的组合符策略是“白名单删除”，不是按 `Mn/Me` 类别一刀切。
4. `forbidden_strings` 只删独立字面量 `\n/\t/\r`，不会删 `\times`、`\theta`、`\right` 这类 LaTeX 命令。
5. QC 模块只检测和报告，不直接修改文本。
6. `meta.cleaning_v5` 是清洗可追溯性的核心，能说明每条数据被哪个规则改了多少、改在哪里。

