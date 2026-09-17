# Quality Check 算子说明

本文档介绍 `quality_checks_V6` 下的字符清洗算子和三个独立的质量检查算子。它们之间没有调用关系，上层框架按需各自调用。

V6.0 策略：不删除整条数据，不因为阿拉伯文、印度文等复杂脚本触发整条判废；只做字符级或片段级命中报告。清洗侧新增 HTML 实体、双向文本控制符、颜文字/装饰符号、装饰组合符白名单等规则；PUA 仍只做高置信上下文修复和已知噪声精确删除，不做全私用区删除；组合符禁止按 Mn/Me 类别一刀切删除。

---

## 目录

- [源码位置](#源码位置)
- [清洗侧新增算子](#清洗侧新增算子)
- [算子一：mojibake\_detect — 乱码检测](#一mojibake_detect--乱码检测)
- [算子二：text\_sub\_dedup — 连续重复去重](#二text_sub_dedup--连续重复去重)
- [算子三：repetition\_check — 对话输出重复检测](#三repetition_check--对话输出重复检测)

---

## 源码位置

| 算子 | 主要文件 | 核心入口 |
|---|---|---|
| `mojibake_detect` | `mojibake_detect/detector.py`、`mojibake_detect/custom_patterns.py`、`mojibake_detect/utils.py` | `MojibakeDetector.exec_text(text)` |
| `text_sub_dedup` | `text_sub_dedup/continuous_dedup_mapper_v7.py` | `detect_split_content(data, language)` |
| `repetition_check` | `repetition_check/loop.py` | `check_repetition_ngram(record)`、`check_token_flood(record)`、`check_runaway_enum(record)` |

---

## 清洗侧新增算子

字符清洗的主入口是 `clean_variation_chars.py` 中的 `clean_text(text,
rules=DEFAULT_RULES)`。清洗动作会写入 `meta.cleaning_v6`（启用
`--add-cleaning-meta` 时），而不是把整条记录判废。

### `repeated_noise_token`

这是连续重复策略中的高置信度清洗例外，不是通用 dedup。一个 token 必须
同时满足：

- 含有字母和数字；
- token 内有至少 4 个连续相同字符；
- 同一 token 连续出现至少 5 次；
- 位于 Markdown fenced code 外。

例如 `a111111111 a111111111 a111111111 a111111111 a111111111` 会被清理；
普通单词、纯数字、年份、一般 ID、只重复 4 次的 token 会保留。当前阈值
在源码中由 `REPEATED_NOISE_TOKEN_MIN_REPEATS` 控制，默认值为 5。

### `hex_blob`

清理高十六进制比例、连续长度至少 64 个字符的疑似十六进制/二进制转储。
`0x` 前缀的代码常量有边界保护，但合法 hash、数据库 ID、URL 参数和机器
数据仍可能满足形状，因此这是当前最需要继续做上下文保护评估的规则。代码
块和合法 64 位 hash 不应在没有样本验证时默认视为噪声。

### 与连续去重的边界

`text_sub_dedup` 发现的普通重复片段仍然只进入 QC 报告；不要把所有命中都
转换成删除动作。`repeated_noise_token` 只处理上述窄模式，避免误删表格、
棋谱、选项、歌词和正常排比结构。

---

## 一、mojibake_detect — 乱码检测

### 作用

检测文本中由编码不匹配产生的乱码和异常 Unicode 字符，标出具体位置。不修改文本，只输出命中报告。

### 入口

```python
from mojibake_detect.detector import MojibakeDetector

detector = MojibakeDetector()
result = detector.exec_text(text)
```

### 输入 / 输出

**输入**：任意字符串。

**输出**：

```python
{
  "hits": [
    {
      "offset": [start, end],        # 命中行在全文中的字符区间
      "detail": {
        "seq": {
          "hit_num": 3,              # 命中字符数
          "hit_ratio": 0.12,         # 命中字符占本行比例
          "hit_positions": "00110100..."  # 与本行等长的 0/1 串，1 表示命中
        },
        # 还可能包含: fffd, cscn, c1_control, double_enc,
        #             cp1252, pua, pua_known_noise, html_entity,
        #             bidi_control, decorative_symbol,
        #             decorative_combining, zw_flood, invis_garbage,
        #             specials, conditional_zw
      }
    }
  ]
}
```

命中为空时 `hits` 为空列表。

### 检测规则一览

| 规则名 | 检测的问题 | 触发条件 |
|---|---|---|
| `fffd` | U+FFFD 替换符（解码失败时插入） | 出现即命中 |
| `seq` | 中文乱码序列（GBK/UTF-8/Latin-1 互相误解码） | 命中已知乱码模式 |
| `cscn` | 代理对/未分配 Unicode 码位 | 出现即命中（可通过 `enable_cscn=False` 关闭） |
| `c1_control` | C1 控制字符（0x80–0x9F），正文中不该出现 | 出现即命中 |
| `double_enc` | UTF-8 字节被二次编码（如 `Ã…` 结构） | 全文命中数 ≥ 5 |
| `cp1252` | UTF-8 被 Windows-1252 错误解读（如 `Ã©`） | 全文命中数 ≥ 3 |
| `pua` | PUA 私用区字符（U+E000–U+F8FF）洪泛 | 全文出现 ≥ 5 个 |
| `pua_known_noise` | 已知 PUA 噪声，如 U+F8FF、U+F04A | 出现即命中 |
| `html_entity` | HTML 实体残留，如 `&gt;`、`&lt;`、`&ndash;`、`&#123;` | 出现即命中 |
| `bidi_control` | 双向文本控制符（U+202A–U+202E、U+2066–U+2069、U+200E/U+200F） | 出现即命中 |
| `decorative_symbol` | 颜文字/装饰符号（U+2500–U+27BF，排除 `□/☐/☑/☒/✓/✗/○/●` 等表单语义符号） | 出现即命中 |
| `decorative_combining` | V6 白名单装饰组合符（U+0336、U+033F、U+035C–U+0361） | 出现即命中 |
| `zw_flood` | 零宽字符混入 | 全文出现 ≥ 1 个 |
| `invis_garbage` | BOM、WORD JOINER、SOFT HYPHEN 等不可见垃圾字符 | 全文出现 ≥ 3 个 |
| `specials` | Specials 区段（U+FFF0–U+FFFC）及行间标注字符 | 出现即命中（零容忍） |
| `conditional_zw` | 条件性零宽字符（ZWNJ、ZWJ、LTR/RTL mark 等） | 全文出现 ≥ 1 个；V6.0 不做整条判废，只报告字符级命中 |
| `variation_selector` | Unicode variation selector（如 U+FE0F） | 出现即命中 |
| `styled_math_alnum` | 数学样式字母数字（如 𝓥、𝕄） | 出现即命中 |
| `abnormal_space` | NBSP、窄 NBSP、EM SPACE、全角空格等 | 出现即命中 |
| `math_symbol_flood` | 数学/符号字符连续洪泛 | QC 检测同一符号连续 ≥ 8；清洗侧按连续 ≥ 4 处理 |
| `control_format_flood` | 格式/行分隔控制字符洪泛（Cf/Zl/Zp） | 全文出现 ≥ 3 个 |

> **为什么部分规则有阈值？**  
> `double_enc`、`cp1252`、`pua` 等字符在西欧语言正文中偶发出现属正常，必须累积到一定数量才说明是系统性编码问题。

### 典型命中 Case

**中文乱码序列（GBK 被 Latin-1 误读）**

```
原文：文字（GBK 编码）
乱码：æ–‡å­—
规则：seq
```

**UTF-8 被 Windows-1252 误读**

```
原文：résumé
乱码：rÃ©sumÃ©（出现多次）
规则：cp1252（命中数 ≥ 3 触发）
```

**解码失败产生替换符**

```
文本：你好�世界�
规则：fffd（每个 � 单独命中）
```

**BOM 混入正文**

```
文本：...正文内容﻿更多内容﻿第三段﻿...
规则：invis_garbage（3 个 BOM 触发）
```

**训练 token / HTML 残留 / HTML 实体**

```
文本：hello<br>&nbsp;&gt;<|im_start|>world
清洗规则：forbidden_strings + html_entity
处理：删除 HTML 残留、训练 token、字面量 \n/\t/\r，以及整个 HTML 实体串
```

**双向文本控制符**

```
文本：‭John‬ 10:7
清洗规则：bidi_control
处理：删除 U+202D/U+202C 等方向控制符，保留正文 John 10:7
```

**颜文字/装饰符号**

```
文本：(╯°□°）╯︵ ┻━┻ ▒▓██
清洗规则：decorative_symbol
处理：删除 U+2500–U+27BF 范围内的制表符、块元素、几何图形、杂项符号、Dingbats
```

**Lenny/Zalgo 装饰组合符**

```
文本：( ͡° ͜ʖ ͡°) / s̶t̶r̶i̶k̶e̶
清洗规则：decorative_combining
处理：只删除 U+0336、U+033F、U+035C–U+0361 白名单组合符，不按 Mn/Me 类别删除
```

**符号分隔线洪泛**

```
文本：标题
=====
清洗规则：math_symbol_flood
处理：同一符号连续 ≥ 4 时删除整段符号
```

**零宽字符刷屏**

```
文本：正文里夹杂了大量 ​（零宽空格）
规则：zw_flood（出现 ≥ 20 个触发）
```

### 典型不命中 / 豁免 Case

**阿拉伯语/希伯来语/印地语中的正常组合符** — V6.0 默认不会按 Mn/Me 类别删除；语言文字所需的元音点号、重音、天城文记号等会保留。

**法语文本中偶发 `é`（`0xC3 0xA9`）** — 不足 3 个不触发 `cp1252`。

**普通中文文本中单个 `涓`** — 单独一个不触发 `seq`（`twohits` 规则要求 ≥ 2 种不同字符同时命中）。

---

## 二、text_sub_dedup — 连续重复去重

### 作用

找出文本中连续重复的片段，输出建议清洗/删除的字符区间。不直接修改文本，由上层决定是否删除。

> ⚠️ 运行时依赖外部 `text_utils` 模块（`get_offset_map`、`remove_punctuation_and_whitespace`、`has_target_char`、`is_code`），该模块不在本目录内，需由上层环境提供。

### 入口

```python
from text_sub_dedup.continuous_dedup_mapper_v7 import detect_split_content

results = detect_split_content(data, language)
# language: "en" 走英文路径，其他值走中文/默认路径
```

### 输入 / 输出

**输入**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `data` | `str` | 待检测的文本 |
| `language` | `str` | `"en"` 为英文，其他为中文/默认 |

**输出**：列表，每条是一个命中结果：

```python
{
  "offset": [mask_start, mask_end],       # 建议删除的区间（保留第一次出现，后续重复区间）
  "score": 3,                             # 重复次数
  "info": {
    "repeat_text_offset": [start, end],   # 重复单元第一次出现的区间
    "char_len": 5,                        # 重复单元中的有效字符数
    "word_len": 2,                        # 英文路径下的有效词数
    "is_special": 0,                      # 1 = \\u0000/\U0000 特殊重复
  },
  "model_type": "sub_dedup_continuous"
}
```

### 核心逻辑

1. **按文本长度切片**（避免超长文本正则爆炸）：

   | 文本长度 | 切片大小 | 重叠 |
   |---|---|---|
   | < 10000 | 10000（不切） | 0 |
   | 10000–300000 | 10000 | 1000 |
   | 300000–500000 | 5000 | 200 |
   | 500000–800000 | 3000 | 100 |
   | > 800000 | 1000 | 10 |

2. **快速预检**：扫一遍看有没有重复迹象，没有就直接跳过，省正则开销。

3. **用正则找重复片段**：
   - 短片段：`(.{m,n}?)\1\1`（至少连续 3 次）
   - 长片段：`(.{m,n}?)\1`（至少连续 2 次）

4. **过滤 + 阈值判断**：V6.0 仍统一只把连续重复 **5 次及以上** 判为异常重复。

### 重复阈值

**中文路径**：

| 重复单元有效汉字数 | 最少重复次数 |
|---|---|
| ≥ 1 个字 | 5 次 |

V6.0 口径：不再按重复单元长度降低阈值；短词、短句、长段都要求连续重复 5 次及以上。

**英文路径**：

| 重复单元有效词数 | 最少重复次数 |
|---|---|
| ≥ 1 个词 | 5 次 |

### 会跳过的场景

- `is_code(data)` 判定为代码
- 英文路径：含 `" song "`、`♫`、`♪`（歌词）；含 `"---------------"`（分隔线）
- 含 `|` 的片段（可能是 Markdown 表格）
- 含 `</ ` 的片段（HTML 标签）
- 同时含 `{` 和 `}` 的片段（代码块/模板）
- `%d`、`%s`（格式字符串）
- 含 `\\u0000`/`\U0000` 的作为特殊 case 单独处理（`is_special=1`）
- 题目解析场景："相关知识点："到"解析"之间出现 3 次的内容

### 典型命中 Case

**中文单字刷屏**

```
原文：哈哈哈哈哈哈哈哈
重复单元："哈"（1字，重复 8 次）
输出：建议删除第 1–8 次中的多余部分
```

**短句重复**

```
原文：高考分数是零分可以复读高考分数是零分可以复读高考分数是零分可以复读
重复单元："高考分数是零分可以复读"（10字，重复 3 次）
输出：不命中；V6.0 需要连续重复 5 次及以上
```

**英文单词重复**

```
原文：the the the the the
重复单元："the"（1词，重复 5 次）
输出：命中
```

**英文短语重复**

```
原文：click here to see more click here to see more click here to see more
重复单元："click here to see more"（5词，重复 3 次）
输出：不命中；V6.0 需要连续重复 5 次及以上
```

**英文短语刷屏**

```
原文：click here click here click here click here click here
重复单元："click here"（2词，重复 5 次）
输出：命中
```

### 典型不命中 Case

**正常的诗词排比**（重复次数未达到阈值）

```
原文：春风春雨春意浓，春花春草春色新
"春" 出现多次但不连续，不触发
```

**Markdown 表格**

```
原文：| 列1 | 列2 | 列3 |
含 | 被过滤
```

---

## 三、repetition_check — 对话输出重复检测

### 作用

检测 LLM assistant 回复中的异常重复输出（n-gram 复读、单 token 刷屏、失控编号枚举）。不修改文本，命中返回原因字符串，未命中返回 `None`。

> **重要**：三个函数都**只检查** `role == "assistant"` 的消息，跳过 user/system。
>
> **思考段处理**：若 assistant 内容含 `</think>` 标签，会自动去掉从开头到第一个 `</think>` 的内容，只检测后面的可见回答。

### 入口

```python
from repetition_check.loop import (
    check_repetition_ngram,
    check_token_flood,
    check_runaway_enum,
)

record = {
    "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
    ]
}

print(check_repetition_ngram(record))  # 命中返回字符串，否则 None
print(check_token_flood(record))
print(check_runaway_enum(record))
```

---

### 函数一：`check_repetition_ngram` — N-gram 重复检测

**检测什么**：assistant 回答中大段内容复读，如一整句话或一整段模板被重复输出。

**最低文本长度**：原始 `content` 和去掉 `</think>` 后的 `visible` 都至少 200 字符。

**检测顺序**（命中即返回，不继续）：

1. **失控计数器**（`_runaway_counter_score`）：检测"编号在递增但正文内容在重复"的情况
   - 找出连续递增数字序列（如 `1,2,3,4,5...`），需至少 5 个连续递增才识别为计数器
   - 把这些数字替换成空格后，再做 10-gram 检测，阈值 `ratio > 0.55`

2. **10-gram 重复**：`ratio > 0.75`（token 数需 ≥ 30）

3. **5-gram 重复**：`ratio > 0.85`（token 数需 ≥ 15）

`ratio` = 出现次数 > 1 的 n-gram 的总出现次数 / 全部 n-gram 数（注意：不是"额外重复次数"）

**命中输出示例**：

```
repetition_ngram:10gram msg[0] ratio=0.80 repeated=80/100 top='这是一个很好的...' x15
repetition_ngram:runaway_enum msg[1] ratio=0.62 repeated=44/71 top='步骤如下...' x8
```

**典型命中 Case**

```
模型输出：
这是一个很好的问题。我来为您详细解答。这是一个很好的问题。我来为您详细解答。
这是一个很好的问题。我来为您详细解答。这是一个很好的问题。我来为您详细解答。
（重复十几次）

→ 10-gram ratio 远超 0.75，命中
```

```
模型输出（失控计数器）：
第1步：您需要打开设置菜单，找到相关选项，点击确认按钮。
第2步：您需要打开设置菜单，找到相关选项，点击确认按钮。
第3步：您需要打开设置菜单，找到相关选项，点击确认按钮。
...
第20步：您需要打开设置菜单，找到相关选项，点击确认按钮。

→ 去掉递增数字后，10-gram ratio 超过 0.55，命中
```

**典型不命中 Case**

```
正常的带编号列表：
1. 第一点内容各有不同
2. 第二点内容也不一样
3. 第三点内容彼此独立

→ 内容不重复，不命中
```

---

### 函数二：`check_token_flood` — 单 Token 洪泛检测

**检测什么**：同一个 token（字符/词）大量重复刷屏。

**最低要求**：`content` 和 `visible` 都至少 200 字符，分词后 token 数至少 20。

**阈值**：

| `visible` 长度 | 触发条件（最高频 token 占所有 token 的比例） |
|---|---|
| > 1000 字符 | `ratio >= 0.60` |
| ≤ 1000 字符 | `ratio >= 0.80` |

**命中输出示例**：

```
repetition_ngram:token_flood msg[0] token='哈' ratio=0.92 (920/1000 tokens)
```

**典型命中 Case**

```
模型输出：
哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈
（几百个哈）

→ "哈" 占全部 token 的 90%+，命中
```

```
模型输出（长文本）：
the the the the the the the the the the the the the the the the the the the ...
（超过 1000 字符，"the" 占比超 60%）

→ 命中
```

**典型不命中 Case**

```
正常分析文本中 "的" 出现频繁：
这是一个很好的分析。这个问题的核心是...的主要原因是...

→ "的" 出现多次，但占比通常不超 40%，不命中
```

```
短文本（≤ 1000 字符）中 "的" 占比 70%：
→ 阈值是 80%，不命中
```

---

### 函数三：`check_runaway_enum` — 失控空枚举检测

**检测什么**：模型输出大量只有编号、没有正文内容的行。

**最低要求**：`content` 至少 100 字符；去掉 `</think>` 后按换行切分，至少 20 行才检查。

**支持的编号格式**：

| 格式 | 示例 |
|---|---|
| 阿拉伯数字点/括号 | `1.` `2)` |
| 全角数字 | `１．` `２）` |
| 中文数字 | `一、` `二。` |
| "第...X" 格式 | `第一、` `第十二：` |
| 括号编号 | `(1)` `（一）` |
| 圈号数字 | `①` `②` … `⑳` |

**注意**：匹配"整行基本只有编号"的行。`1. 有内容` 不算空枚举行。编号不需要递增，重复的 `1.` 也计入。

**触发条件（满足其一）**：

1. 连续空枚举行 ≥ 30 行
2. 总空枚举行 ≥ 20 行 **且** 空枚举行占所有行的比例 ≥ 50%

**命中输出示例**：

```
repetition_ngram:runaway_enum msg[0] consecutive=35 total_enum=40/50 lines
repetition_ngram:runaway_enum msg[1] ratio=0.55 (22/40 lines)
```

**典型命中 Case**

```
模型输出（失控枚举）：
1.
2.
3.
4.
5.
...
31.
32.

→ 连续空枚举行 32 ≥ 30，命中
```

```
模型输出（枚举占比高）：
（一）
（二）
（三）
这里有一句正文
（四）
（五）
...（共 40 行，其中 22 行是空编号行）

→ 22 ≥ 20 且 22/40 = 55% ≥ 50%，命中
```

**典型不命中 Case**

```
正常的有内容编号列表：
1. 第一步，打开应用
2. 第二步，进入设置
3. 第三步，点击确认

→ 每行都有内容，不是空枚举，不命中
```

```
编号行少于 20 个且不连续：
1. 内容
...（10 行正文）
2. 内容
...（10 行正文）

→ 空枚举行为 0（有内容），不命中
```

---

## 四、三个算子对比

| | mojibake_detect | text_sub_dedup | repetition_check |
|---|---|---|---|
| **检测目标** | 编码错误/异常 Unicode | 文本内连续重复片段 | LLM 输出复读/刷屏/失控枚举 |
| **输入** | 任意字符串 | 任意字符串 + 语言标识 | 含 `messages` 列表的 dict |
| **输出** | 命中行的位置和规则详情 | 建议删除区间的列表 | 原因字符串或 `None` |
| **是否修改文本** | 否 | 否（由上层决定） | 否 |
| **是否有外部依赖** | 无（只用标准库 + numpy） | 需要外部 `text_utils` | 无（只用标准库） |
| **粒度** | 字符级，精确到每个字符 | 片段级，输出 offset 区间 | 消息级，只输出原因 |
| **处理对象** | 任意文本 | 任意文本 | 只检查 `role=assistant` 的消息 |
