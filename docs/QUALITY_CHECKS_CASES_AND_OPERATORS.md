# Quality Checks 清洗与质检案例、算子和交接手册

> 文档日期：2026-08-26  
> 当前可运行基线：quality_checks_V6 / v6.0  
> 交接目标：V7（当前还没有独立的 quality_checks_V7 目录）  
> 适用场景：给大模型训练的长文本、文章、评论、JSONL 和 XLSX 数据

这份文档是本项目的案例总账和算子说明。它回答四个问题：

1. 我们实际遇到过哪些脏数据和误报；
2. 每个清洗算子和 QC 算子到底做什么；
3. 哪些问题可以自动改，哪些问题只能先报告；
4. 下一位维护者如何复现、验收和继续开发。

文档以当前源码的真实行为为准。历史文档中如果仍写着 V5、旧阈值或旧的多语种豁免逻辑，应以 quality_checks_V6 中的代码为准。

---

## 0. 先看结论

### 0.1 当前版本边界

当前可以直接运行和交付的是：

~~~text
quality_checks_V6/
  clean_variation_chars.py
  run_long_dialog_quality_checks.py
  quality_api.py
  mojibake_detect/
  repetition_check/
  text_sub_dedup/
~~~

text_sub_dedup/continuous_dedup_mapper_v7.py 中的 v7 只是历史文件名，不代表整个项目已经完成 V7。接手时不要把 V6 压缩包改名后直接对外称为 V7。

### 0.2 已确定的业务原则

| 原则 | 当前决定 |
|---|---|
| 是否删除整条 JSONL 记录 | 不删除。只修改指定字段中的字符或片段 |
| 清洗与质检关系 | 清洗器改写高置信度噪声；QC 只报告复杂问题 |
| 通用连续重复 | 只报告，不自动删除 |
| repeated_noise_token | 仅处理一个非常窄的、高置信度的重复噪声形状 |
| PUA | 不删除整个私用区；已知噪声精确处理，未知值保留并报告 |
| 多语种 | 不做“阿拉伯文/小语种整条判废”；也不按 Mn/Me 类别一刀切 |
| emoji | 当前业务取舍是默认删除 emoji 及其组合附件，即使存在少量语言/符号语义风险 |
| 原始数据 | 输入 JSONL/XLSX 不覆盖，输出和报告另存 |
| 溯源 | JSONL 建议写 meta.cleaning_v6，按算子聚合连续 span |

### 0.3 一句话判断标准

~~~text
高置信字符级噪声 -> 清洗
高置信但有语义替代的字符 -> 上下文替换
未知 PUA / 乱码 / 通用重复 / 可能是合法 hash -> 报告并人工或专门流程处理
~~~

---

## 1. 项目目标与处理边界

### 1.1 目标

数据来自网页、PDF/OCR、电子书、导出系统、对话生成和内部拼接流程。主要任务是：

- 去除不可见控制字符和编码残留；
- 规范化异常空格、行分隔符和数学样式字符；
- 删除明确的 emoji、装饰符号、HTML 残留和训练特殊 token；
- 修复少量能够从上下文确定含义的 PUA 字符；
- 发现乱码、重复、枚举跑飞和连续片段重复；
- 输出可回溯的原文位置、规则、动作和短预览。

### 1.2 不负责的事情

当前 V6 不是通用文本纠错器，也不是完整的编码恢复系统。它不会：

- 根据上下文自动把所有乱码还原成原文；
- 判断文章事实是否正确；
- 判断一段重复究竟是文学、教材、歌词还是模型失控；
- 对整条记录做删除判决；
- 自动识别和重写所有小语种；
- 证明任意 64 位十六进制串一定是噪声；
- 递归清洗 JSON 对象内所有嵌套字段。

---

## 2. 总体架构

推荐处理链路如下：

~~~text
原始 JSONL / XLSX
        |
        | 可选：文章评论结构预处理
        v
clean_article_comment.py
        |
        v
quality_checks_V6/clean_variation_chars.py
        |
        +--> cleaned JSONL + meta.cleaning_v6
        +--> cleaned XLSX + clean_summary.json
        |
        v
run_long_dialog_quality_checks.py
        |
        +--> issues.jsonl
        +--> failed_records.jsonl
        +--> summary.json
        |
        v
人工抽样、规则调参、训练集交付
~~~

### 2.1 清洗链路

核心入口：

| 层级 | 文件/函数 | 作用 |
|---|---|---|
| 单文本 | clean_text(text, rules) | 清洗一个字符串，返回新文本和字符级变更 |
| 单 JSON 记录 | clean_record(record, ...) | 清洗指定字段并构造 metadata |
| JSONL 文件 | clean_jsonl(...) | 逐行清洗，保持输出顺序 |
| XLSX 文件 | clean_xlsx(...) | 按表头名称清洗单元格 |

默认只处理名为 content 的顶层字符串字段。传入 --fields 后才处理其他顶层字段；非字符串值跳过，记录本身保留。

### 2.2 QC 链路

核心入口：

| QC 模块 | 入口 | 是否修改正文 |
|---|---|---|
| mojibake_detect | MojibakeDetector.exec_text(text) | 否 |
| text_sub_dedup | detect_split_content(data, language) | 否 |
| repetition_check | check_repetition_ngram、check_token_flood、check_runaway_enum | 否 |

QC 输出 issues 和 highlights。failed=true 只表示“被规则标记”，不等于应当删除整条数据。

### 2.3 API 链路

Python 入口：

~~~python
from quality_checks_V6 import clean_record, check_record, clean_and_check
~~~

HTTP 接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /health | 查看服务是否存活 |
| GET | /v1/rules | 查看当前清洗规则和 QC 类型 |
| POST | /v1/clean | 清洗一条 JSON 记录 |
| POST | /v1/check | 检查一条 JSON 记录 |
| POST | /v1/clean-and-check | 清洗后再检查一条记录 |

API 是单条记录接口。大文件应使用 CLI；把几十 MB 的 JSONL 一次塞进 HTTP 不是当前设计目标。

---

## 3. 版本演进和每次解决的问题

工作区中的版本是并列目录和压缩包，不是 Git 分支；当前工作区没有可依赖的 Git 历史。

| 版本 | 主要变化 | 当前定位 |
|---|---|---|
| 早期 quality_checks | 以检测为主，包含乱码、重复等基础模块 | 历史基线 |
| V2 | 基础 variation selector 和数学样式字符处理 | 历史版本 |
| V3 | 增加 emoji、组合符等字符清洗 | 历史版本 |
| V3.5 | 加入 PUA 上下文修复、控制字符、换行和元信息实验 | 历史生产实验 |
| V4 | 尝试更细的 emoji/复杂脚本保护与配置 | 历史实验 |
| V5 | 完整清洗链路、前后 QC、API、compact metadata、hex 规则 | 历史基线/兼容版本 |
| V6 | 在 V5 基础上加入 repeated_noise_token，元信息改为 cleaning_v6 | 当前基线 |
| V7 | 还未建立独立目录 | 下一阶段 |

### 3.1 V3/V3.5 时代暴露的核心问题

早期版本存在几个明显风险：

- 数学样式字母曾被直接删除，造成语义损失；
- 组合符按类别处理时可能伤害阿拉伯文、希伯来文、印地文等；
- emoji 的 base、variation selector、ZWJ 被分别处理时可能留下残片；
- 通用连续重复检测容易把教材、歌词、诗歌和表格当成脏数据；
- 只看单字符而不看上下文时，PUA 无法安全替换。

### 3.2 V5/V6 的收敛方向

后来形成了现在的分层策略：

1. 字符级高置信噪声可以直接清洗；
2. 有明确语义映射的 PUA 才在上下文中替换；
3. 通用 dedup、乱码恢复和疑似 hash 先报告；
4. 每条 JSONL 记录保留按规则聚合的变更元信息；
5. 清洗前后都跑 QC，比较问题是否减少而不是只看清洗字符数。

---

## 4. V6 默认清洗算子总表

源码权威顺序是 quality_checks_V6/clean_variation_chars.py 中的 DEFAULT_RULES：

~~~text
variation_selector
styled_math_alnum
abnormal_space
bidi_control
pua_known_noise
pua_contextual
decorative_symbol
decorative_combining
emoji_all
control_invisible
line_separator
html_entity
forbidden_strings
repeated_noise_token
hex_blob
math_symbol_flood
line_boundary_space
blank_line_collapse
~~~

| 算子 | 类型 | 当前动作 |
|---|---|---|
| variation_selector | 字符 | 删除 Unicode variation selector |
| styled_math_alnum | 字符 | NFKC 转成普通字母/数字 |
| abnormal_space | 字符 | 异常空格替换或删除 |
| bidi_control | 字符 | 删除双向文本控制符 |
| pua_known_noise | 字符 | 删除已确认的 PUA 噪声 |
| pua_contextual | 上下文字符 | 按语境替换/删除少量 PUA |
| decorative_symbol | 字符 | 删除装饰符号，保留部分表单语义符号 |
| decorative_combining | 字符 | 删除明确白名单中的装饰组合符 |
| emoji_all | 序列/字符 | 删除 emoji 和附件字符 |
| control_invisible | 字符 | 删除 BOM、零宽和其他不可见控制字符 |
| line_separator | 字符 | U+2028 转换为换行，U+2029 转换为段落换行 |
| html_entity | span | 删除 HTML 实体完整字符串 |
| forbidden_strings | span | 删除训练 token、HTML 残留和部分错误字面量转义 |
| repeated_noise_token | span | 删除高置信度重复的可疑字母数字 token |
| hex_blob | span | 删除符合形状的长十六进制块 |
| math_symbol_flood | span | 删除连续重复的数学/符号字符 |
| line_boundary_space | 输出单元 | 删除行尾和文本尾空格/Tab |
| blank_line_collapse | 输出单元 | 连续换行最多保留两个 |

下面逐个说明规则、真实例子、边界和风险。

---

## 5. 清洗算子详解

### 5.1 variation_selector

**作用**

删除：

- U+FE00-U+FE0F；
- 补充区 variation selector U+E0100-U+E01EF。

默认只删除 selector，不删除前面的普通字符。

**例子**

~~~text
汉 + U+E0100  -> 汉
文本 + U+FE0F -> 文本
~~~

**为什么需要它**

selector 通常只负责选择字形，来自网页、字体或复制流程时可能成为无意义的隐藏字符。保留 base 字符通常比连 base 一起删更安全。

**重要边界**

1. 规则本身不知道某个 selector 是否是合法的汉字异体字选择；
2. 如果 selector 紧跟 emoji-like base，并且 emoji_all 开启，clean_text 会把 base 和 selector 一起标记为 emoji；
3. 因此它与 emoji_all 组合时不再只是“删 selector”。

U+FE0F 出现在 【️】 中时，括号不是 selector，也不在清洗范围内；被删的是括号里面的 selector，所以会留下空的 【】。

---

### 5.2 styled_math_alnum

**作用**

处理数学样式字母、数字和相关字形，范围为 U+1D400-U+1D7FF，使用 Unicode NFKC 规范化，而不是直接删除。

**实际案例**

用户曾发现 metadata 中有：

~~~text
𝙤
U+1D664 MATHEMATICAL SANS-SERIF BOLD ITALIC SMALL O
~~~

旧逻辑把这类字符当成异常字符删除。现在的结果是：

~~~text
𝙤 -> o
𝓥 -> V
~~~

**为什么这样改**

这些字符常常只是字体变体，承载的仍是字母或数字语义。NFKC 能保留内容，同时减少字形噪声。

**边界**

- NFKC 是规范化，不是语义理解；
- 只处理代码范围内的字符；
- 不要把“出现数学样式字符”直接当成乱码；
- QC 仍会报告 styled_math_alnum，因为它负责提示清洗前存在过这类字符。

---

### 5.3 abnormal_space

**作用**

处理 PDF/OCR、网页复制和全角排版造成的异常空格：

| 字符 | 码点 | 动作 |
|---|---|---|
| NO-BREAK SPACE | U+00A0 | 换成普通空格 |
| EM SPACE | U+2003 | 换成普通空格 |
| IDEOGRAPHIC SPACE | U+3000 | 换成普通空格 |
| NARROW NO-BREAK SPACE | U+202F | 删除 |
| THIN SPACE | U+2009 | 删除 |

**例子**

~~~text
A　B -> A B
A B -> A B
A B -> AB
A B -> AB
~~~

**边界**

- 不会全局把多个普通空格压成一个；
- 正文中间的 Tab 也不由这个规则处理；
- 窄空格和 thin space 的删除可能影响排版语义，若数据包含高质量排版或科学出版物，需要抽样确认。

---

### 5.4 bidi_control

**作用**

删除可能改变显示方向、但自身不可见的双向文本控制符：

- U+202A-U+202E：embedding、override、pop；
- U+2066-U+2069：directional isolate；
- U+200E：LRM；
- U+200F：RLM。

**例子**

~~~text
U+202E + John + U+202C  -> John
~~~

**风险**

双向控制符在合法的阿拉伯文、希伯来文混排文本中可能用于排版。当前训练数据策略仍选择删除它们，因为目标是保留可见正文、避免隐藏顺序控制进入模型；这不是对所有国际化文本都最保守的排版方案。

U+200E/U+200F 同时出现在 control_invisible 的集合中。由于字符分类有优先级，默认运行时它们通常记为 bidi_control。

---

### 5.5 pua_known_noise

**作用**

只删除已经在样本中确认是噪声的私用区字符：

| 字符 | 码点 | 当前动作 |
|---|---|---|
| Apple logo 私用字形 | U+F8FF | 删除 |
| 字体私有笑脸/噪声 | U+F04A | 删除 |

**例子**

~~~text
价格说明 -> 价格说明
~~~

**为什么不删除整个 PUA**

PUA（Private Use Area）没有跨数据源统一含义。同一个码点可能在不同字体、电子书、业务系统中表示 bullet、点号、撇号、缺字或完全不同的符号。把 U+E000-U+F8FF 一刀切会把未知但有用的内容一起删掉。

---

### 5.6 pua_contextual

**作用**

只对少量已知码点做上下文判断。当前实现位于 _scan_pua_contextual。

| 码点 | 典型原始形态 | 条件 | 替换 |
|---|---|---|---|
| U+F0B7 | 行首项目符号 | 行首且后面有正文 | - 或 - |
| U+F07E | 10 + 私有符号 + 20 | 两侧像数字范围 | ~ |
| U+E010 | 1 + 私有点号 + 中文/正文 | 编号或正文点号 | ． |
| U+E011 | 英文字符 + 私有符号 + 英文字符 | 英文专名中间 | - |
| U+E10B | 英文字符 + 私有符号 + 英文字符 | 英文撇号位置 | ' |
| U+E11A/E11B/E11C | 电子书脚注 marker | 已确认的脚注标记 | 删除 |
| U+E618 | 项目符号 | 行首或正文中的 bullet | -、\n- 或带空格的版本 |
| U+E1BD | ... + 私有符号 + 束 | 特定高置信缺字上下文 | 约 |

**实际案例**

~~~text
U+F0B7 第一项       -> - 第一项
10U+F07E20          -> 10~20
1U+E010选数游戏     -> 1．选数游戏
KU+E011Dick         -> K-Dick
JohnU+E10BDoe       -> John'Doe
方法U+E1BD束        -> 方法约束
~~~

**为什么必须看上下文**

U+F0B7 在很多字体里是 bullet，但不能证明所有数据源里的 U+F0B7 都是 bullet。当前只在行首、后方有正文时替换；正文中间或上下文不明确时保留。

**未知 PUA 的处理**

未知 PUA 不替换、不删除，交给 mojibake_detect 的 pua 或人工抽样。新增映射时必须同时提供：

1. 至少一个真实正例；
2. 一个同码点的反例或不应替换的上下文；
3. 清洗前后样例；
4. 单元测试和误删保护；
5. 数据源和字体来源说明。

---

### 5.7 decorative_symbol

**作用**

默认覆盖：

- Box Drawing：U+2500-U+257F
- Block Elements：U+2580-U+259F
- Geometric Shapes：U+25A0-U+25FF
- Miscellaneous Symbols：U+2600-U+26FF
- Dingbats：U+2700-U+27BF

目标是清掉网页分隔线、块状装饰、颜文字组件和无训练价值的图形符号。

**保护的语义表单符号**

以下字符在分类时优先保留：

~~~text
□ ☐ ☑ ☒ ✓ ✔ ✗ ✘ ○ ● ◯
~~~

它们可能表达问卷选项、单选/复选状态、判断结果或列表语义。

**重要已知问题：▪**

▪ 是 U+25AA BLACK SMALL SQUARE。它落在装饰符号范围内，且不在当前语义表单白名单里，所以默认会被 decorative_symbol 删除。它不是当前代码保护的表单符号。

这正是后面 ▪【️】案例产生语义损失的关键之一。V7 应考虑：

- 在行首 bullet 上下文中把 U+25AA 替换成 -；
- 或只保护明确的列表语境；
- 不能简单把整个 U+25A0-U+25FF 都加入保留名单，因为其中仍有大量图形噪声。

---

### 5.8 decorative_combining

**作用**

只删除精确白名单中的装饰组合符：

~~~text
U+0336
U+033F
U+035C-U+0361
~~~

**例子**

~~~text
s̶t̶r̶i̶k̶e̶ -> strike
~~~

**多语种保护原则**

不能写成“删除所有 Unicode 类别为 Mn、Mc 或 Me 的字符”。这些类别包含：

- 阿拉伯文元音和连接相关标记；
- 希伯来文元音点；
- 天城文、孟加拉文、泰文等书写系统的组合标记；
- 法语重音分解形式，如 e + U+0301。

V6 默认只按白名单删除，不按 Unicode 类别粗删。

---

### 5.9 emoji_all

**作用**

当前策略是删除 emoji 本体和附属字符，覆盖：

- U+1F000-U+1FFFF；
- U+2600-U+27BF；
- U+2B00-U+2BFF；
- keycap/enclosing 相关 U+20D0-U+20FF；
- ZWJ U+200D；
- emoji variation sequence 的 base 和 selector。

**组合序列例子**

~~~text
😂       -> ""
🏳‍🌈     -> ""
1️⃣      -> ""
❤️       -> ""
~~~

不能只逐个删字符后留下半截序列。当前实现通过 variation base 和 keycap 附件的特殊处理，尽量把可见 emoji 单元一起删掉。

**与小语种的冲突**

这是一个业务取舍，不是 Unicode 的普适结论：

- 阿拉伯语、波斯语、印度文字中可能使用 ZWJ/ZWNJ 影响连接；
- 符号和 variation selector 在不同数据源中可能不是表情；
- U+20D0-U+20FF 中的部分组合符也可能服务于数学或符号记法。

当前项目已经决定：不做整条小语种判废，emoji 和相关不可见连接符仍按训练数据清洗策略删除。若数据要用于严格的多语种语言学任务，必须建立单独的语言保护模式。

---

### 5.10 control_invisible

**作用**

删除正文中通常不应出现的不可见字符：

| 码点 | 名称/用途 |
|---|---|
| U+FEFF | BOM / ZERO WIDTH NO-BREAK SPACE |
| U+200B | ZERO WIDTH SPACE |
| U+200C | ZERO WIDTH NON-JOINER |
| U+200D | ZERO WIDTH JOINER |
| U+200E/U+200F | LTR/RTL mark（实际常由 bidi_control 先记） |
| U+2060 | WORD JOINER |
| U+2061 | FUNCTION APPLICATION |
| U+034F | COMBINING GRAPHEME JOINER |
| U+00AD | SOFT HYPHEN |

**BOM 的纠正**

之前讨论中说过“FEEF/BOM”。正确码点是 U+FEFF，不是 U+FEEF。文件开头的 BOM 可以是编码标记；混进正文时通常是污染。

**例子**

~~~text
a​b       -> ab
a‌b       -> ab
a﻿b       -> ab
~~~

字符最终在 metadata 中记为 control_invisible、bidi_control 或 emoji_all，取决于分类优先级和启用的规则。

---

### 5.11 line_separator

**作用**

把 Unicode 行/段分隔符转换成普通换行：

| 字符 | 码点 | 替换 |
|---|---|---|
| LINE SEPARATOR | U+2028 | \n |
| PARAGRAPH SEPARATOR | U+2029 | \n\n |

**例子**

~~~text
第一段U+2028第二段 -> 第一段
                     第二段
A U+2029 B           -> A

                         B
~~~

这是结构替换，不是删除。替换之后可能由 blank_line_collapse 继续压缩多余换行。

**U+2019 不属于这个规则**

U+2019 RIGHT SINGLE QUOTATION MARK 是合法的右单引号，常见于英文缩写和所有格。它不是行分隔符，默认不删除。

---

### 5.12 html_entity

**作用**

匹配并删除整个 HTML 实体：

- 命名实体：&gt;、&amp;、&nbsp;；
- 十进制实体：&#123;；
- 十六进制实体：&#x1F;。

**当前行为**

当前是删除，不是解码：

~~~text
&gt;    -> ""
&#123; -> ""
~~~

因此如果实体本来承载的是有意义的标点，清洗后会丢失该标点。未来若需要保留语义，应新增一个明确的 html_unescape 策略，不能默默改变现有规则的含义。

**匹配边界**

当前正则对实体名称、十进制和十六进制长度有上限；畸形或超长实体可能不会命中，需要由 QC 或新规则补充。

---

### 5.13 forbidden_strings

**作用**

删除已知的结构残留：

~~~text
<br> </br> <br/> <br />
&lt;br&gt; &lt;/br&gt;
<|im_start|> <|im_end|> <|endoftext|>
<|fim_prefix|> <|fim_middle|> <|fim_suffix|>
<arg_key> </arg_key> <arg_value> </arg_value>
<is_displaying_contents> </is_displaying_contents>
U+0003 U+0004
~~~

也尝试删除错误保留下来的字面量 \n、\t、\r，但有一个很重要的保护条件：

~~~text
\\{1,2}[ntr](?![A-Za-z{])
~~~

也就是说，反斜杠后的 n/t/r 如果紧跟英文字母或 {，当前实现会保护它，以免破坏：

~~~text
\times
\theta
\right
\r{a}
C:\temp
~~~

**容易误读的实际行为**

由于保护条件，当前代码中：

~~~text
hello\nworld  -> 通常保留
hello\n world -> 删除 \n
hello\n       -> 删除 \n
~~~

旧 README 中把所有 hello\nworld 都写成会删除，是简化描述，不应替代源码验证。未来如要处理所有字面量转义，应设计更明确的上下文规则并增加 LaTeX、路径、JSON 字符串回归样例。

---

### 5.14 repeated_noise_token

**作用**

这是 V6 新增的窄规则，用来处理类似网页/模型输出中的可疑重复 token，而不是通用 dedup。

必须同时满足：

1. token 是 5 到 128 个 ASCII 字母/数字；
2. token 同时含字母和数字；
3. token 内有至少 4 个连续相同字符；
4. 同一个 token 连续出现至少 5 次；
5. token 之间只由空白、逗号、分号、中文逗号/顿号或 | 等分隔；
6. 命中位置不在 Markdown 三反引号代码块内。

**典型案例**

~~~text
a111111111 a111111111 a111111111 a111111111 a111111111 1. Introduction
~~~

会清洗为：

~~~text
1. Introduction
~~~

这也是用户贴出的论文文本中 a111111111 洪泛的处理方向。

**明确保护的案例**

~~~text
普通普通普通普通普通          -> 不命中此规则
2024 2024 2024 2024 2024       -> 保留，纯数字且无字母
00000000 x5                    -> 保留，纯数字
abc12345 x5                    -> 保留，没有内部四连字符
代码块内：`a111111111 ...` 重复 5 次 -> 保留
同一 token 只重复 4 次          -> 保留
~~~

**metadata 计数陷阱**

rule_counts.repeated_noise_token 统计的是删除/替换的字符单元数，不是 token 数。五个 a111111111 加分隔空格可能显示 count=55，这不表示发现了 55 个 token。

---

### 5.15 hex_blob

**作用**

删除形状上像十六进制转储或二进制 dump 的长块。

当前实际条件：

- 独立连续十六进制字符长度至少 64；
- 或由空格/换行包裹的高 hex 比例块满足至少 64 个 hex 字符；
- wrapped 形式的 hex 比例至少约 0.95；
- 至少有一行包含 64 个 hex 字符；
- 字符前后通常需要非字母数字边界。

**边界例子**

~~~text
连续 63 个 hex 字符 -> 保留
连续 64 个 hex 字符 -> 可能删除
value=0xabab...     -> 由于边界保护通常保留
~~~

**当前高风险**

源码注释有“保留常见 hash”的表述，但当前实现并没有真正完成合法 hash 的通用豁免。以下内容可能误命中：

- 64 位 hash；
- 数据库 ID；
- URL 参数；
- 机器生成的标识；
- 代码或日志中的独立十六进制值。

特别是 hash=aaaaaaaa... 后面的长串仍可能被删除，因为等号提供了边界。V7 必须优先加入上下文保护，而不是只继续调长度阈值。

**当前处置建议**

- 训练文本中确定是 dump 的长块：可以保留默认删除；
- 代码、URL、JSON key/value、hash、数据库字段：先用自定义规则或人工抽样；
- 任何阈值变化都要用合法 hash 反例回归；
- 不要把“64 位”理解成“64 bytes”，这里按 Python 字符偏移和字符数量判断。

---

### 5.16 math_symbol_flood

**作用**

删除同一个数学/符号字符连续重复至少 4 次的洪泛：

- Unicode 类别为 Sm 或 So 的符号；
- 额外的 = + ~ × ═。

**例子**

~~~text
====       -> ""
=====      -> ""
×××××      -> ""
连续 8 个波浪号 -> ""
~~~

**边界**

- 只删同一个字符的连续 run；
- 普通单个等号、加号、数学公式不删；
- 清洗侧阈值是 4；
- QC 的 math_symbol_flood 阈值是连续 8，二者不要混淆；
- ！！！！不一定命中，因为感叹号不是当前额外集合的同类数学洪泛目标。

---

### 5.17 line_boundary_space

**作用**

在前面规则已经完成替换之后，删除：

- 换行前的空格/Tab；
- 文本末尾的空格/Tab；
- 由空格撑出来的伪空行。

**案例**

~~~text
上一段\n \n下一段
特此通知。 \n上海市市场监督管理局
编:市民\t章:食品安全\t节:综合管理\t\n
~~~

清洗后：

~~~text
上一段\n\n下一段
特此通知。\n上海市市场监督管理局
编:市民\t章:食品安全\t节:综合管理\n
~~~

**边界**

- 不会全局压缩正文中间的多个空格；
- 不会把正文中间的 Tab 全部替换成空格；
- 只处理处于行边界的空格/Tab；
- 它必须在前面规则之后运行，因为 U+2029 等可能先生成换行。

---

### 5.18 blank_line_collapse

**作用**

连续换行最多保留两个：

~~~text
A\n\n\nB       -> A\n\nB
A\n\n\n\nB     -> A\n\nB
~~~

它解决网页抽取、书评和 Markdown 中常见的空白洪泛，同时保留段落边界。

**边界**

- 不删除所有换行；
- 不把段落合并成一行；
- 由 U+2029 生成的两个换行也会参与后续判断；
- 当前实现按有效输出单元扫描，因此会考虑前面替换产生的结果。

---

### 5.19 可选 legacy 算子

以下规则存在于 OPTIONAL_LEGACY_RULES，但不属于默认 V6：

#### combining_decoration

一个 base 字符后连续挂至少 2 个 Mn/Mc/Me 组合标记时，删除组合标记、保留 base；开头没有 base 的组合标记也会被删。

它比白名单更激进。只有在明确知道数据中不存在复杂脚本、且已经完成人工抽样时才考虑启用。

#### exotic_combining

删除少见组合符范围，例如：

- U+1AB0-U+1AFF；
- U+1DC0-U+1DFF；
- U+FE20-U+FE2F；
- 少量明确列出的字符。

同样不建议默认启用。--rules all 只代表默认 V6 规则，不自动包含这两个 legacy 规则。

---

## 6. 规则优先级和重叠行为

清洗器不是把所有规则独立跑完再简单拼接；classify_char 有固定优先级，随后还有 PUA、span 和输出单元级扫描。

### 6.1 单字符分类大致顺序

当前顺序包括：

1. variation selector；
2. bidi control；
3. decorative combining；
4. known PUA；
5. semantic form symbol 保护；
6. decorative symbol；
7. emoji/attachment；
8. invisible control；
9. line separator；
10. styled math；
11. exotic combining；
12. abnormal space。

因此同一个字符只会在 metadata 中优先归到一个主要规则，除非后续序列逻辑再给它加上关联行为。

### 6.2 典型重叠

| 输入 | 当前主要归类 | 结果 |
|---|---|---|
| ▪ | decorative_symbol | 删除 |
| ▪️ | decorative_symbol + variation_selector | base 和 selector 都删除 |
| ❤️ | decorative_symbol + variation_selector | 心和 selector 都删除 |
| 🏳‍🌈 | emoji_all | 整个序列删除 |
| 1️⃣ | emoji_all + variation selector | 数字、selector、keycap 附件一起删除 |
| ✓ | semantic form protection | 单独通常保留 |
| ✓️ | variation sequence hook | 当前会把 base 也标为 emoji_all，最终可能删除 |

这种重叠必须通过真实 fixture 验证，不能只看规则名字推测。

---

## 7. mojibake_detect 详解：检测什么、为什么不自动改

### 7.1 定位和边界

mojibake_detect 是 QC 检测器，不是编码恢复器。它的职责是回答：

1. 这段文本是否出现了编码错配或异常 Unicode；
2. 哪一行、哪几个字符触发了哪一条子规则；
3. 是否值得进入人工复核或单独的修复流程。

它不会根据一个命中结果猜测原文，也不会把整条记录删除。原因是乱码恢复通常不是一一对应的替换：同一串异常字符可能来自不同的原始编码，错误解码还可能已经丢失信息。当前 V6 只对可以明确判断的字符噪声使用清洗器；经典乱码序列仍然只报告。

源码入口：

~~~text
quality_checks_V6/mojibake_detect/detector.py
MojibakeDetector.exec_text(text)
~~~

### 7.2 当前子规则和阈值总表

| 子规则 | 检测目标 | 当前阈值/触发方式 | 是否由 V6 清洗器直接处理 |
|---|---|---|---|
| fffd | U+FFFD 替换字符 | 出现即命中 | 否，先报告 |
| seq | 已知中文乱码序列 | 单次模式或 two-hit 条件 | 否，先报告 |
| cscn | Unicode 类别 Cs/Cn | 出现即命中，可关闭 | 否，先报告 |
| c1_control | U+0080-U+009F C1 控制区 | 出现即命中 | 否，先报告 |
| double_enc | UTF-8 被二次编码的结构 | 全文匹配数至少 5 | 否，先报告 |
| cp1252 | UTF-8 被 Windows-1252 误解码 | 全文匹配数至少 3 | 否，先报告 |
| pua | BMP PUA 洪泛 | U+E000-U+F8FF 总数至少 5 | 部分已知值由清洗器处理 |
| pua_known_noise | 已确认的 PUA 噪声 | 出现即命中 | 是，精确删除 |
| html_entity | HTML 命名/数字实体 | 出现即命中 | 是，当前删除整个实体 |
| bidi_control | 双向文本控制符 | 出现即命中 | 是，删除 |
| decorative_symbol | 装饰符号 | 出现即命中，表单白名单除外 | 是，删除 |
| decorative_combining | 装饰组合符白名单 | 出现即命中 | 是，删除 |
| zw_flood | 零宽/不可见字符合集 | 当前阈值为 1 | 清洗器按具体字符规则处理 |
| invis_garbage | BOM、WORD JOINER 等 | 总数至少 3 | 清洗器按具体字符规则处理 |
| specials | Specials 和 interlinear 字符 | 出现即命中 | 否，先报告 |
| conditional_zw | ZWJ/ZWNJ/LRM/RLM 等 | 当前阈值为 1，无豁免 | 清洗器按当前 emoji/控制策略处理 |
| variation_selector | 变体选择符 | 出现即命中 | 是，删除 selector |
| styled_math_alnum | 数学样式字母数字 | 出现即命中 | 是，NFKC 转普通字符 |
| abnormal_space | 异常空格 | 出现即命中 | 是，替换或删除 |
| math_symbol_flood | 同一数学/符号字符连续刷屏 | 连续长度至少 8 | 清洗侧阈值为 4 |
| control_format_flood | Cf/Zl/Zp 控制/格式字符总量 | 总数至少 3 | 具体字符部分处理 |

这里的“命中字符数”要谨慎解释。一个字符可能同时属于多个集合，例如 U+200D 既属于 conditional_zw，也属于 zw_flood，还可能被 emoji 序列逻辑视为附件。summarize_mojibake 会按规则分别累计，所以 issue 中的 hit_chars 不是去重后的物理字符数。

### 7.3 fffd：替换字符

U+FFFD 的显示通常是 �。它是解码失败时由程序插入的替换符，不是原文字符。

~~~text
你好�世界
~~~

处理结论：

- QC：出现即标记；
- 清洗：默认不删除，因为删除它不能恢复丢失的原字；
- 修复：需要回到原始字节、来源文件或上游编码环节。

如果只把 � 删除，文本可能从“你好�世界”变成“你好世界”，读起来似乎正常，但中间可能丢了一整个词或数字，不应把这种动作误认为编码修复。

### 7.4 seq：已知中文乱码序列

custom_patterns.py 中保留了一批从历史数据收集的 GBK、UTF-8、Latin-1 互相误解码特征，例如：

~~~text
æ–‡å­—
鎴戜滑
浠€涔堝苟
~~~

当前有两类匹配：

1. one-hit：命中一个高置信字符串就标记整段；
2. two-hit：从一组疑似乱码首字符中，至少出现两种不同字符后，才标记这些匹配位置。

two-hit 的实现有一个需要交接人知道的细节：它只把匹配位置的首字符置为 true，并不总是把完整乱码词组置为 true。因此 QC 的位置掩码可能比人工理解的“整个乱码片段”更窄。这是历史兼容实现，不应据此直接生成删除区间。

典型误报来源：

- 某些合法汉字偶然落入历史字符集合；
- 文本很短，只有一个可疑字符；
- 不同来源的乱码表现形式不在当前词表中。

V7 如要增强，建议先将模式改为带置信度的 span 输出，并保存匹配字符串和来源规则，不要直接扩大删除范围。

### 7.5 cscn：Cs/Cn 字符

Unicode 类别 Cs 是 surrogate，Cn 是未分配码位。正常 Python Unicode 字符串里通常不应出现它们，但损坏的 JSON、转码中间结果或孤立代理项可能带入。

~~~text
<孤立 surrogate 或未分配码位>
~~~

当前 enable_cscn 默认开启，命中即报告。它不负责修复，因为需要知道这些码位原本对应的编码字节。读取 JSONL 时还要注意：某些孤立 surrogate 无法稳定地用 UTF-8 写回，必须在输入读取和输出阶段单独处理。

### 7.6 c1_control：实际残留案例

C1 控制区是 U+0080-U+009F。它们不是普通可见文本，在错误的单字节编码转换中经常出现。

800 条 sample_100_per_domain 的 V6 清洗后，仍有 1 条 mojibake 失败记录。它是英文课程文本，命中：

~~~text
U+0091
U+0093
U+0094
rule = c1_control
~~~

这三个字符分别位于较长的正文中。它们没有被清洗器删除，因为当前 clean_variation_chars.py 没有 c1_control 清洗规则，只有 QC 检测规则。这个案例直接说明：

~~~text
QC 能找到，不等于清洗器已经能改。
~~~

后续若要清洗 C1，必须先确认是否有从原始字节恢复的可能；对于确定为控制噪声的值，可以新增窄规则，但需要保留原文和回归反例。

### 7.7 double_enc：双重编码结构

检测器用三组结构正则表示 UTF-8 字节被再次当成 Latin-1 一类字符解读的形态：

- 二字节结构：一个 C0/C1 扩展区样式字符后跟 continuation 区样式字符；
- 三字节结构：一个更高位前导字符后跟两个 continuation 样式字符；
- 四字节结构：一个四字节前导字符后跟三个 continuation 样式字符。

全部匹配数达到 5 才标记，目的在于避免把西欧正文中偶然出现的字符当作系统性乱码。

### 7.8 cp1252：Windows-1252 误解码

典型形态：

~~~text
résumé -> rÃ©sumÃ©
“text” -> âtextâ
— -> â
~~~

当前至少需要 3 个匹配才触发。单独出现一个合法的 é、ñ 或 € 不会因为它们本身而被判定为乱码。反过来，低于阈值也不代表一定正确，只代表当前检测器不够确信。

V6 不自动调用 ftfy 或其他恢复库改写这些文本，原因是：

- 修复库可能根据启发式改变合法的混合编码文本；
- 修复后的字符串需要和原始字节、来源编码一起验证；
- 训练数据交付需要可回滚，不能只保留“修复后”。

推荐做法是把外部库作为候选修复器，输出 before/after/diff 和置信度，先做离线抽样。

### 7.9 pua：私用区检测

当前 pua 子规则只统计 BMP 私用区 U+E000-U+F8FF，达到 5 个才报告“PUA 洪泛”。这和清洗器的 pua_known_noise、pua_contextual 是三件不同的事：

| 名称 | 作用 |
|---|---|
| pua | 发现一条文本里 PUA 是否多到可疑 |
| pua_known_noise | 删除已经确认是噪声的具体码点 |
| pua_contextual | 根据前后字符修复少数具体码点 |

未知 PUA 不因 pua 命中就自动删除。见第 10.7 节的 PUA 案例。

### 7.10 html_entity、bidi 和装饰字符

这些子规则与清洗器有直接对应关系：

- html_entity：识别 &gt;、&amp;、&#123;、&#x1F; 等完整实体；
- bidi_control：识别 embedding、override、isolate、LRM、RLM；
- decorative_symbol：识别 box drawing、block、geometric、misc symbols、dingbats；
- decorative_combining：只识别 V6 白名单，而不是所有 Mn/Me。

需要特别注意规则重叠。比如 ▪️ 中的 U+25AA 可能由 decorative_symbol 命中，U+FE0F 由 variation_selector 命中；这不是两个独立的可见字符问题，而是一个序列的两个组成部分。

### 7.11 zw_flood、invis_garbage 和 conditional_zw

历史版本的文档曾写过更高阈值和复杂脚本豁免，但当前 V6 代码真实值是：

~~~text
zw_flood       = 1
conditional_zw = 1
invis_garbage  = 3
~~~

当前没有按阿拉伯文、印地文或东南亚文字占比进行豁免。原因是当时的业务决定是：不做整条记录删除判断，即使 emoji/零宽策略可能影响小语种，也按字符级策略继续清理。

这三个规则的关系：

- U+200B/U+200C/U+200D 会被 conditional_zw 和 zw_flood 同时看到；
- U+FEFF/U+2060/U+2061/U+00AD 等会被 zw_flood 看到，但只有累计 3 个才会被 invis_garbage 单独报告；
- U+200E/U+200F 在 bidi_control 中优先归为双向控制；
- QC 结果可能出现多个规则同时命中同一个位置。

### 7.12 specials 和 control_format_flood

U+FFF0-U+FFFC 是 Specials/保留区，U+FFF9-U+FFFB 是 interlinear annotation 相关字符。自然语言正文中出现它们通常是抽取或编码残留，当前零容忍报告。

control_format_flood 统计 Unicode 类别 Cf、Zl、Zp 的字符总数，达到 3 才把全部位置标记。它是“数量型提示”，不是单独的修复方案；具体字符应交给 bidi、line_separator、control_invisible 等清洗算子。

### 7.13 检测输出的行窗口语义

exec_text 会先按换行切成窗口：

~~~text
原文
  -> 每行一个窗口
  -> 旧 seq/fffd/cscn 在窗口内计算
  -> 新规则先对全文计算，再切片到窗口
~~~

因此 hits 中的 offset 常常是整行的 [start, end]，而不是只包住命中字符。真正的字符位置在 detail.rule.hit_positions 中。下游生成 Excel 时必须使用掩码或 chars 信息定位，不能直接把 hit 的整行当作问题片段。

---

## 8. text_sub_dedup 详解：连续重复是不是 n-gram

### 8.1 一句话回答

它可以被宽泛地称为“连续重复 n-gram 检测”，但它不是标准的滑动窗口 n-gram 频率统计。

两者区别：

| 项目 | text_sub_dedup | repetition_check 的 n-gram |
|---|---|---|
| 核心方法 | 正则 backreference 找相邻重复子串 | 对 token 序列做滑动窗口计数 |
| 关注范围 | 某一段是否紧接着重复 | 全段 n-gram 重复比例 |
| 中文 token | 清除标点空白后按字符/片段 | CJK 字符逐字符 token |
| 英文 token | 按空格和短语处理 | 非 CJK 连续串按空白切词 |
| 输出 | 具体建议 mask 区间 | 消息级原因字符串 |
| 是否改正文 | 不改，输出建议区间 | 不改，只报告 |

所以用户看到 a111111111 或“表示。表示。表示。”时，命中的通常是 sub_dedup_continuous；这不代表它使用了语言模型意义上的 n-gram。

### 8.2 入口、分支和依赖

~~~text
quality_checks_V6/text_sub_dedup/continuous_dedup_mapper_v7.py
detect_split_content(data, language)
~~~

language 等于 en 时走英文路径，其他值走中文/默认路径。当前 V6 包已经把 text_utils.py 一起带入，包含 offset map、代码识别、目标语言字符判断等辅助函数；旧版文档中“需要外部 text_utils”的描述只适用于早期孤立拷贝。

### 8.3 长文本切片

按原文字符长度选择分片参数：

| 文本长度 | 子片段大小 | 重叠长度 |
|---:|---:|---:|
| 0-9999 | 10000 | 0 |
| 10000-299999 | 10000 | 1000 |
| 300000-499999 | 5000 | 200 |
| 500000-799999 | 3000 | 100 |
| 800000 以上 | 1000 | 10 |

重叠用于减少重复正好跨分片边界时的漏检，但也会带来同一问题在相邻片段重复返回的可能。上层应按 offset 合并或去重。

### 8.4 中文/默认路径

处理步骤：

1. 如果全文包含 code:: 或 Markdown 三反引号代码块，直接跳过；
2. remove_punctuation_and_whitespace 删除中文标点、ASCII 标点和所有空白，得到检测串；
3. 快速预检：
   - 单字符连续 5 次；
   - 短片段可重复迹象；
   - 长片段重复迹象；
4. 用正则查找：
   - 短片段至少连续 3 次的候选；
   - 长片段至少连续 2 次的候选；
5. 用 get_repeat_num_and_end 计算真实连续次数；
6. 最终 check_cn_standard 要求 repeat_num 至少 5，且重复单元里有中文字符；
7. 把清洗串 offset 映射回原文 offset；
8. 根据段落起点调整“保留第一次、mask 后续”的范围。

这里很容易产生认知偏差：正则候选阶段可以看到 3 次，但最终输出通常仍要求 5 次。候选阈值和业务判定阈值不是同一个数字。

### 8.5 英文路径

英文路径保留空格，以词为主要单位：

1. 代码块、歌词提示 song/♫/♪、长横线分隔线跳过；
2. 快速预检检查单词 5 次或二词片段连续重复；
3. 正则寻找短/长重复片段；
4. 根据空格拆分并统计有效英文词；
5. check_en_standard_char 永远返回 false，实际由 check_en_standard_word 判断；
6. 最终仍要求重复次数至少 5；
7. 含 |、HTML 结束标签、同时含花括号、格式串 %d/%s 的片段跳过；
8. 生成原文 mask offset。

英文分支有一个历史特征：正则允许从任意字符位置开始，所以可能输出像 ead, d、orange, 或 over and 这样的片段。它们是定位算法找到的重复窗口，不一定是自然语言词边界。

### 8.6 输出结构

~~~json
{
  "offset": [mask_start, mask_end],
  "score": 5,
  "info": {
    "repeat_text_offset": [first_start, first_end],
    "char_len": 2,
    "word_len": 0,
    "is_special": 0
  },
  "model_type": "sub_dedup_continuous"
}
~~~

字段含义：

- offset：建议 mask 的区间，通常保留第一次出现，指向后续重复；
- score：连续出现次数；
- repeat_text_offset：第一次重复单元的原文区间；
- char_len：有效字符数量；
- word_len：英文路径的有效词数；
- is_special：1 表示历史上针对字面量 \u0000/\U0000 的特殊重复；
- model_type：固定标识。

当前 QC 只报告这个结果，不执行 offset 删除。因为 offset 是“建议处理区间”，不是经过语义审核的删除命令。

### 8.7 阈值 3、4、5 到底分别是什么

这是交接中最容易混淆的部分：

| 数字 | 出现位置 | 含义 |
|---:|---|---|
| 3 | 短片段正则候选 | 连续 3 次先进入候选，不代表最终失败 |
| 4 | 某些历史版本/清洗侧的局部规则 | 例如 math_symbol_flood 清洗阈值 |
| 5 | 当前 text_sub_dedup 最终连续重复标准 | 5 次及以上才作为异常重复返回 |
| 8 | QC math_symbol_flood | 同一符号连续 8 次才报告 |

不要把“重复 3 次的候选”理解成“阈值是 3”。V6 的通用连续重复最终标准是 5。

### 8.8 800 条样本中实际残留的合法/可疑重复

V6 清洗后仍有 16 条 sub_dedup_continuous。它们不是同一种脏数据：

| 行号 | 语言 | 片段概览 | 次数 | 初步判断 |
|---:|---|---|---:|---|
| 18 | cn | 表示。 | 5 | 教材语句结构，需人工看上下文 |
| 60 | en | orange, | 5 | 颜色/列表或抽取重复 |
| 73 | en | one, two, three | 8 | 课堂讲解/枚举，可能合法 |
| 73 | en | answer, / this one, | 5 | 口语讲解中的自然重复可能 |
| 81 | en | over and | 5 | 诗歌、歌词或口语，定位片段偏短 |
| 170 | en | on and | 6 | 口语/歌词可能 |
| 180 | en | too | 6 | 自然语言强调或抽取异常 |
| 448 | en | dead, | 9 | 可能是歌词/诗歌，且 offset 从词中间开始 |
| 755/756 | cn | 哈 | 7 | 拟声/笑声，也可能是刷屏 |
| 760 | cn | 朝、长 | 7 | 教材或排版重复，需要上下文 |
| 768 | cn | 测验常识判断题、测验常识判断题解 | 7 | 题库排版/目录重复可能 |
| 769-772 | cn | “向我走1步” | 10 | 课堂口令/练习题重复可能 |
| 794 | cn | 嘎 | 6 | 拟声词或异常重复 |

这张表的结论不是“全部放过”，而是说明自动删除需要更丰富的结构特征。对于歌词、教材、课堂指令、诗歌、表格、棋谱和拟声词，重复本身可能就是内容。

### 8.9 旧实现的误报案例

此前 100 条复查中出现：

~~~text
右 左 右左 右 左右 左
~~~

上下文像表格或演示：

~~~text
男 右 左 右 左 右 左右
女 右 左右 左 右左 右 左右 左
~~~

算法因为去掉标点/空白后看到短字符反复出现而命中，但这不等于模型刷屏。处置方式是：

- 保留正文；
- 在 QC 中标记为疑似重复；
- V7 增加表格、棋谱、选项、左右方向等结构识别；
- 对 char_len 很小的结果降级为 warning，而不是直接 failed。

### 8.10 为什么不能把所有 dedup 结果自动清洗

自动删除至少会面对四种风险：

1. 文学修辞：诗歌、歌词、拟声词故意重复；
2. 教学结构：逐步演示和课堂口令重复；
3. 排版结构：目录、表格、题库行重复；
4. 定位不稳定：正则可能从词中间开始，删除区间边界不符合语义单位。

因此 V6 只增加了一个非常窄的 repeated_noise_token 清洗例外，见第 5.14 节；它不能替代通用 dedup。

---

## 9. repetition_check 详解：面向 assistant 的生成异常

### 9.1 与 text_sub_dedup 的区别

repetition_check 处理的是长对话记录中的 assistant 输出异常，输入通常是：

~~~json
{
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
~~~

三个函数都只检查 role 等于 assistant 的消息，不检查 user/system。检测器只返回原因字符串或 None，不修改正文，也不返回可直接删除的区间。

如果 assistant 内容包含 </think>，检测器会去掉从开头到第一个 </think> 的思考段，只对后面的可见回答检查。可见回答不足最低长度时跳过。

### 9.2 混合语言分词

当前 _tokenize 的规则：

- 中文、日文假名、韩文范围内的字符各自成为一个 token；
- 其他字符先按空白切成连续 token；
- 独立的中日韩/全角标点 token 被过滤；
- ASCII 标点不会全部过滤，以保持 LaTeX 结构等场景的可计算性。

因此同一句文本在中文和英文中 token 数量不同，ratio 不能跨语言直接比较。

### 9.3 check_repetition_ngram

最低条件：

- 原始 content 长度至少 200；
- 去掉思考段后的 visible 长度也至少 200；
- n-gram token 数够形成至少 3 个窗口。

检测顺序：

1. runaway counter：先处理连续递增数字；
2. 10-gram：ratio 大于 0.75；
3. 5-gram：ratio 大于 0.85。

ratio 定义为：

~~~text
所有出现次数大于 1 的 n-gram 出现总次数
------------------------------------
全部 n-gram 窗口数
~~~

它不是“重复多出来的次数 / 总次数”。例如一个 n-gram 出现 5 次，分子记 5，不记 4。

命中示例：

~~~text
这是一个很好的问题。我来为您详细解答。
这是一个很好的问题。我来为您详细解答。
这是一个很好的问题。我来为您详细解答。
...
~~~

正常不命中示例：

~~~text
1. 第一点内容各有不同
2. 第二点内容也不一样
3. 第三点内容彼此独立
~~~

### 9.4 runaway counter

检测器找出独立的 1 到 6 位数字，至少有 5 个连续递增数字才形成计数器。它把这些数字替换为空格，再对结果做 10-gram 重复检测，阈值是 ratio 大于 0.55。

典型模型失控：

~~~text
第1步：打开设置菜单，找到相关选项。
第2步：打开设置菜单，找到相关选项。
第3步：打开设置菜单，找到相关选项。
...
第20步：打开设置菜单，找到相关选项。
~~~

数字本身不同，但正文完全复用；这是普通 n-gram 可能漏掉的形状。

注意：数字递增本身不是坏数据。真正触发的是去掉数字后仍有高比例重复。

### 9.5 check_token_flood

最低条件：

- content 和 visible 至少 200 字符；
- token 数至少 20。

计算最高频 token 占比：

| visible 长度 | 触发比例 |
|---:|---:|
| 不超过 1000 字符 | 至少 0.80 |
| 超过 1000 字符 | 至少 0.60 |

典型命中：

~~~text
哈哈哈哈哈哈哈哈哈哈哈哈...
~~~

或者：

~~~text
the the the the the the the ...
~~~

正常正文中“的”出现较多并不等于 flood，通常占比达不到阈值。该规则是消息级提示，不能把最高频 token 直接从正文全部删除，因为它可能是合法高频词。

### 9.6 check_runaway_enum

最低条件：

- content 至少 100 字符；
- 可见文本按换行切分后至少 20 行。

支持：

- 1.、2)；
- 全角数字编号；
- 一、二。；
- 第一、或第十二：；
- （1）、(一)；
- ① 到圈号编号。

只有“整行基本只有编号”的行才算空枚举行，1. 有正文不算。

触发条件二选一：

1. 连续空枚举行至少 30 行；
2. 总空枚举行至少 20 行，并且占全部行至少 50%。

正常的编号列表不命中：

~~~text
1. 打开应用
2. 进入设置
3. 点击确认
~~~

### 9.7 repetition 的处置

repetition 命中表示生成质量可疑，不表示字符一定错误。建议分三级：

| 结果 | 处置 |
|---|---|
| token flood 或 30 行空编号 | 高风险，人工确认后通常剔除回答部分 |
| 高 ratio n-gram | 检查是否模板、引用、歌词或教材 |
| 低长度/短词重复 | 只做 warning，不能直接删除 |

V6 的批量 QC 只写 issues.jsonl 和 highlights，保留原始失败记录。

---

## 10. 历史案例手册：我们遇到过什么，最后怎么处理

这一章是交接时最重要的部分。每个案例都按“现象、根因、当前动作、是否建议继续改”记录。看到相似输入时，先按这里的处置原则判断，不要只看某个 Unicode 名称就扩大规则。

### 10.1 ▪【️】：为什么会留下空的 【】

原始字符序列可以拆成：

~~~text
▪   U+25AA BLACK SMALL SQUARE
【   U+3010 LEFT BLACK LENTICULAR BRACKET
️   U+FE0F VARIATION SELECTOR-16
】   U+3011 RIGHT BLACK LENTICULAR BRACKET
~~~

当前 V6 默认规则的处理：

1. U+25AA 落在 decorative_symbol 的几何符号范围，当前不在 semantic form 白名单中，所以删除；
2. U+FE0F 是 variation_selector，删除；
3. U+3010/U+3011 不在当前装饰符号范围，且它们是 Excel/人工标注常用的可见括号，所以保留。

结果：

~~~text
▪【️】  ->  【】
~~~

这不是括号算子把内容“洗没了”，而是原文的“内容”只有一个不可见的 variation selector；可见的 ▪ 在括号外，并且被装饰符号规则删除。若人眼把 ▪ 理解成括号内的图标，视觉上就会觉得括号空了。

当前结论：

- 【】本身不删除，因为它是标注边界；
- V6 不尝试根据空括号反推缺失图标；
- 若业务确定行首 ▪ 是列表符号，V7 可以在列表上下文中把它替换为 -，而不是删除；
- 不应把所有 U+25AA 全局改成 -，因为它也可能是图形、复选状态或排版符号。

相关变体：

| 输入 | V6 默认结果 | 原因 |
|---|---|---|
| ▪ | 空串 | decorative_symbol |
| ▪️ | 空串 | base 被 decorative_symbol 删除，FE0F 被 variation_selector 删除 |
| ❤️ | 空串 | 心形落入装饰范围，FE0F 另行删除 |
| ✓ | ✓ | semantic form 白名单保护 |
| ✓️ | 空串 | FE0F 触发 emoji variation hook，连 emoji-like base 一起删除 |
| 【️】 | 【】 | 只有 selector 被删除 |

这个案例说明：单字符规则和序列规则必须一起看；metadata 里可能出现两个相邻 span，而人工看到的是一个“空框”问题。

### 10.2 𝙤：为什么原来被删，现在应转成普通字母

字符：

~~~text
𝙤
U+1D664 MATHEMATICAL SANS-SERIF BOLD ITALIC SMALL O
~~~

它不是乱码，而是 Unicode 数学样式字母。旧版本把样式字符当作噪声直接删除，可能把 g𝙤od 变成 god。V6 的 styled_math_alnum 使用 NFKC：

~~~text
𝙤 -> o
𝓥 -> V
𝔽 -> F
~~~

处置原则：

- 能通过规范化保留字母/数字语义时，优先替换；
- 不要把整个 U+1D400-U+1D7FF 区间当作“非法字符”删除；
- QC 可以继续报告它曾出现，清洗器负责把它变成普通字符。

### 10.3 hank you：原文没有丢 t，是可视化定位代码的问题

曾在“问题片段明细表”的上下文可视化中看到：

~~~text
hank you
~~~

但原文实际是：

~~~text
thank you
~~~

这个现象不是 clean_variation_chars 删除了 t。根因在旧版 QC 可视化路径：

1. repetition 检测器返回的是可读的 reason 字符串；
2. _find_repetition_offset 再从 reason 里解析 top n-gram/token；
3. 它用 text.find(candidate) 反查原文，而不是使用检测器产生的权威 offset；
4. 候选可能被截断、去掉首尾空格，或者从重复窗口内部开始；
5. Excel 只把反查到的区间包上标记，于是可能把 thank you 的第二个字符开始显示成 hank you。

因此要区分：

| 层面 | 是否发生变化 |
|---|---|
| 原始 content | 没有删除 t |
| QC reason | 可能只保存了截断的 n-gram |
| highlight offset | 可能偏移或落在词中间 |
| Excel 可视化 | 错误地把偏移区间显示成 hank you |

正确修复方向：

- repetition checker 直接返回结构化的绝对 offset；
- sub_dedup 使用已有的 repeat_text_offset 和 offset，不重新解析 reason；
- 可视化前断言 text[start:end] 与 detector 的原始命中一致；
- 增加回归测试：包含 thank you 的重复输入，标注区间必须从 t 开始；
- 如果只能兼容旧 reason，至少用完整重复单元和边界校验，不要对字符串做裸 find。

交接时看到“标注片段少了首字母”，先查 offset 和可视化，不要立即修改清洗规则。

### 10.4 a111111111：论文抽取中的重复噪声

在用户贴出的 TAVI 论文文本中出现：

~~~text
a111111111 a111111111 a111111111 a111111111 a111111111 1. Introduction
~~~

它看起来像 PDF/网页抽取时混入的页码或内部标记洪泛，不是正常论文句子。V6 的 repeated_noise_token 只有在以下条件同时成立时才处理：

- token 长度 5-128；
- 同时含字母和数字；
- token 内有至少 4 个连续相同字符；
- 同一 token 连续至少 5 次；
- 中间只有允许的空白/标点分隔；
- 不在 Markdown 三反引号代码块中。

结果：

~~~text
a111111111 ... x5 1. Introduction
  -> 1. Introduction
~~~

这个规则不是通用 dedup。以下都保留：

~~~text
a111111111                       # 只出现一次
a111111111 a111111111 x4         # 低于五次
2024 2024 2024 2024 2024         # 纯数字
abc12345 abc12345 abc12345 x5    # 没有内部四连字符
~~~

代码块内也保留，因为代码、日志和测试 fixture 可能故意重复这个 token。未来若真实数据出现重复的业务 ID，仍需增加字段/上下文保护。

### 10.5 论文长文中的 a111... 是否算 n-gram

它可以被连续重复检测看到，但更准确的归类是：

- 形状检测：repeated_noise_token；
- 通用 QC：sub_dedup_continuous；
- 不是 repetition_check 的滑动 5-gram/10-gram 统计。

处理策略是先用窄规则清理高置信 token，再对清洗结果跑通用 QC。这样不会因为一个论文里的正常句子重复就扩大删除范围。

### 10.6 emoji 复合序列：不能只删一个字符

常见输入：

~~~text
🏳‍🌈
👨‍👩‍👧
1️⃣
❤️
™️
~~~

组成可能包括：

- emoji base；
- U+FE0F variation selector；
- U+200D ZERO WIDTH JOINER；
- U+20E3 COMBINING ENCLOSING KEYCAP；
- U+20E4 COMBINING ENCLOSING SQUARE；
- 肤色修饰符、旗帜区域指示符等。

V6 的业务决定是把 emoji 及其附件当作训练噪声删除。emoji_all 对 ZWJ、keycap 附件和常见 variation base 做联动处理，目标是避免留下孤立 selector、ZWJ 或半个图标。

已知边界：

- emoji_all 的范围是宽范围 Unicode 区间，不是完整的标准 Emoji 属性表；
- U+2600-U+27BF 中混有普通符号、装饰符号和语义符号；
- semantic form 白名单与 variation hook 叠加时可能出现 ✓️ 整体被删；
- 如果数据用途是多语种语言学或数学排版，当前策略不够保守。

V7 理想方向：

1. 用 Unicode grapheme cluster 或 emoji 库按用户感知字符分组；
2. 提供 strict、language_safe、keep_symbols 等模式；
3. 在元信息中记录整个序列，而不是只记录每个附件字符；
4. 对 ZWJ/ZWNJ 的语言语义做独立策略。

### 10.7 小语种、ZWJ/ZWNJ：为什么会冲突，当前为什么仍然清理

这是项目中最重要的取舍之一。零宽字符不是一个“看到就一定是垃圾”的集合：同一个码点在不同语言中可能有不同作用。

| 字符 | 码点 | 可能的正常用途 | 当前 V6 行为 |
|---|---|---|---|
| ZERO WIDTH SPACE | U+200B | 泰文、高棉文、缅甸文、老挝文分词或断行 | 清洗；QC 的 `zw_flood`/`conditional_zw` 可报告 |
| ZERO WIDTH NON-JOINER | U+200C | 波斯语、阿拉伯语、乌尔都语和部分印度文字的连接控制 | 清洗；当前没有语言豁免 |
| ZERO WIDTH JOINER | U+200D | 印度文字合字、emoji ZWJ 序列 | emoji 序列中由 `emoji_all` 清洗；单独也属于不可见控制 |
| LRM/RLM | U+200E/U+200F | 混合方向排版 | `bidi_control` 优先处理并删除 |

历史上曾考虑过“文本含阿拉伯文/印地文时跳过零宽字符”的逻辑。后来业务决定变为：

1. 不因为小语种出现就删除整条记录；
2. 清洗仍按字符或序列执行；
3. 即使少数 ZWJ/ZWNJ 可能有语言语义，也不让它们阻止当前训练数据的 emoji/不可见噪声清理。

因此必须把下面两句话分开理解：

~~~text
“这条记录中有小语种”       != “这条记录整体不合格”
“这个零宽字符可能有语义”   != “当前 strict 模式一定保留它”
~~~

当前 QC 阈值为 `zw_flood=1`、`conditional_zw=1`，是为了发现问题，不是为了做整条判废。当前清洗器的默认规则则会按具体字符集合删除 U+200B-U+200F/U+2060-U+2061 等不可见字符；`emoji_all` 还会联动删除 emoji 序列里的 ZWJ 和附件。

**建议的后续模式**

V7 不应再用一个全局布尔开关解决所有语言。建议提供三种配置，并在输出中记录模式名：

| 模式 | 适用场景 | ZWJ/ZWNJ 策略 |
|---|---|---|
| `strict_training` | 当前默认训练数据清洗 | emoji 和不可见控制优先清除 |
| `language_safe` | 多语种语言学、翻译、语料研究 | 保留语言上下文中的 ZWJ/ZWNJ，单独报告 |
| `symbol_preserving` | 数学、出版物、排版数据 | 保留可能有符号语义的序列，人工复核 |

在模式没有真正实现前，不要在 README 中宣称已经有语言安全模式。

### 10.8 PUA：已知映射、上下文映射和未知值

PUA（Private Use Area，私用区）是给字体或应用自行定义的码位，不是一个跨数据源统一的字符表。BMP 常见范围是 U+E000-U+F8FF；检测器当前的 `pua` 子规则也只统计这一范围。

**实际遇到的形态**

| 形态 | 示例 | 当前处理 |
|---|---|---|
| 已知纯噪声 | U+F8FF Apple logo、U+F04A 私有笑脸 | `pua_known_noise` 精确删除 |
| 行首项目符号 | U+F0B7 + “第一项” | `pua_contextual` 在行首替换为 `-`/`- ` |
| 数字范围符 | `10` + U+F07E + `20` | 在两侧像数字时替换为 `~` |
| 电子书标点 | U+E010、U+E011、U+E10B | 仅在数字/英文邻接关系成立时替换 |
| 脚注 marker | U+E11A/U+E11B/U+E11C | 已知电子书上下文中删除 |
| 未知 PUA | 任意未登记私用码位 | 保留正文，交给 QC/人工复核 |

**为什么 U+F0B7 不能全局替换**

在某个字体中 U+F0B7 可能显示成 bullet，但另一个数据源可能让它表示缺字、特殊标点或业务图标。因此当前条件是“行首、后面有正文”才做列表符号替换。即便如此，这仍然是一个数据源相关的启发式，不是 Unicode 标准映射。

新增 PUA 映射前必须同时提交：

1. 来自真实数据源的正例和原始字体/编码来源；
2. 同一码点不应替换的反例；
3. 替换前后文本和上下文；
4. 单元测试、误删测试和统计结果；
5. 可回滚的映射表版本。

**PUA 洪泛和 PUA 清洗不是一件事**

~~~text
pua                 = 发现一条文本中 PUA 是否多到可疑（默认 >= 5）
pua_known_noise     = 删除已确认的具体 PUA（出现即处理）
pua_contextual      = 根据上下文修复少数登记过的具体 PUA
~~~

`pua` 命中并不授权删除全部私用区字符。未知 PUA 保留，是为了避免把不同数据源的有效符号一并损坏。

### 10.9 `meta_private_info`：为什么里面的 emoji 没有被清理

用户曾在第 184 行发现 `meta_private_info` 内还有大量 emoji。这不是 `emoji_all` 失效，而是字段范围不同。

样本中该字段的实际形态是一个顶层字符串，内容类似：

~~~json
{
  "meta_private_info": "{\"comments\":[{\"user_name\":\"paula ☾\", ...}]}"
}
~~~

V6 默认命令是：

~~~text
--fields content
~~~

清洗器只遍历命令中指定的顶层字段；它不会递归扫描整个 JSON，也不会因为字段名字包含 `meta` 就自动处理。于是：

| 字段 | 默认是否清洗 | 原因 |
|---|---|---|
| `content` | 是 | 默认业务正文 |
| `meta_private_info` | 否 | 数据集元信息/评论附属字段，不在默认 fields |
| 嵌套 JSON 对象内部的 `comment` | 否 | 当前不是递归清洗器 |
| 非字符串字段 | 否 | `clean_text` 只接收字符串 |

如果业务明确允许清理该字段，可以显式指定：

~~~bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.jsonl \
  --output output.content_and_private.cleaned.jsonl \
  --format jsonl \
  --fields content,meta_private_info \
  --add-cleaning-meta \
  --meta-detail spans
~~~

但这会改变评论、用户名或私有元信息，不能因为看到 emoji 就默认执行。更稳妥的方案是先把该字段解析成结构化对象，明确哪些子字段是正文，再单独处理并记录字段路径。

这也解释了为什么 metadata 中的 `fields` 是重要信息：它记录“本次实际上允许清洗哪些字段”，不是“记录里所有字段都已经检查过”。

### 10.10 BOM、零宽字符和“FEEF”记忆错误

正确的 BOM 码点是 **U+FEFF**，不是 U+FEEF。U+FEFF 有两种常见语境：

1. 文件开头的 UTF-8 BOM，作为编码签名存在；
2. 正文中间的 ZERO WIDTH NO-BREAK SPACE，通常是复制、拼接或转码残留。

在训练正文中，第二种情况通常没有可见语义，V6 的 `control_invisible` 会删除它。相关字符包括：

| 字符 | 码点 | 当前动作 |
|---|---|---|
| ZERO WIDTH SPACE | U+200B | 删除 |
| ZERO WIDTH NON-JOINER | U+200C | 删除 |
| ZERO WIDTH JOINER | U+200D | 由 emoji/控制规则处理 |
| LRM/RLM | U+200E/U+200F | `bidi_control` 删除 |
| WORD JOINER | U+2060 | 删除 |
| FUNCTION APPLICATION | U+2061 | 删除 |
| COMBINING GRAPHEME JOINER | U+034F | 删除 |
| SOFT HYPHEN | U+00AD | 删除 |

**一个字符可能被多个 QC 集合看到**

例如 U+200D 同时属于 `conditional_zw` 和 `zw_flood`，并且在 emoji 序列中还是连接附件。因此 QC 统计按规则累计时可能大于物理字符去重后的数量；这不是重复删除正文。

**为什么不能删除所有组合字符**

Unicode 类别 `Mn`、`Mc`、`Me` 中包含阿拉伯语元音标记、希伯来语 niqqud、天城文和其他文字系统的必要组合符。V6 只删除明确白名单中的 `decorative_combining`，没有启用按类别粗删。两个 legacy 规则仍可选，但不应作为默认训练链路。

### 10.11 U+2028、U+2029 和 U+2019 的区别

这几个码点曾在清洗结果里一起被讨论，但功能完全不同：

| 码点 | Unicode 名称 | 类型 | V6 动作 |
|---|---|---|---|
| U+2028 | LINE SEPARATOR | 行分隔符 | 替换为一个普通 `\\n` |
| U+2029 | PARAGRAPH SEPARATOR | 段落分隔符 | 替换为两个普通 `\\n` |
| U+2019 | RIGHT SINGLE QUOTATION MARK | 合法右单引号 | 保留 |

例子：

~~~text
第一段<U+2028>第二段  -> 第一段\n第二段
A<U+2029>B              -> A\n\nB
John’s book            -> 保留 U+2019
~~~

`line_separator` 是结构规范化，不是简单删除。转换后，`line_boundary_space` 和 `blank_line_collapse` 还可能继续处理行尾空格或超过两行的连续换行。英文缩写和所有格中的 U+2019 不应被当成乱码或换行符。

### 10.12 LaTeX、HTML entity 和字面量转义：为什么不能只搜反斜杠

数据中同时存在真正的 LaTeX、HTML 抽取残留和“本来应该换行却被转义成两个普通字符”的错误。V6 把它们拆开处理。

#### HTML entity

`html_entity` 匹配命名、十进制和十六进制实体，例如：

~~~text
&gt;       &amp;       &nbsp;
&#123;     &#x1F;     &ndash;
~~~

当前动作是删除完整实体串，而不是调用 `html.unescape` 解码。因此 `&gt;` 会变成空串，不会变成 `>`。这是历史业务决定，优点是可以去掉网页残留，缺点是可能损失原本有意义的标点。若未来要保留语义，应新增独立的 `html_unescape` 模式，不能静默改写 `html_entity` 的含义。

#### forbidden_strings

它删除已登记的结构残留：`<br>`、`</br>`、`<|im_start|>`、`<|im_end|>`、`<|endoftext|>`、工具调用标签、控制字节 `U+0003/U+0004` 等，也尝试删除孤立的字面量 `\\n`、`\\t`、`\\r`。

但 `LITERAL_ESCAPE_PATTERN` 有保护条件：反斜杠后的 `n/t/r` 如果紧跟英文字母或 `{`，当前会保留，以避免破坏：

~~~text
\\times       LaTeX 乘号命令，保留
\\theta       LaTeX 希腊字母命令，保留
\\right       LaTeX 定界符命令，保留
\\r{a}         LaTeX 重音命令，保留
C:\\temp        文件路径样式，保留
~~~

因此当前实际行为可能是：

~~~text
hello\\nworld  -> 通常保留（n 后面是字母）
hello\\n world -> 删除字面量 \\n
hello\\n       -> 删除字面量 \\n
~~~

这是一项已知折中。不能仅凭 README 中的简化示例判断结果，必须用源码和回归样例验证。

### 10.13 十六进制乱码和“连续 64 位”规则

用户曾提供图片并询问能否清理十六进制乱码。V6 后来把连续十六进制块的最低阈值收敛到 **64 个字符**：

~~~text
连续 63 个 [0-9A-Fa-f] -> 保留
连续 64 个以上         -> 命中 hex_blob，删除该块
~~~

当前 `hex_blob` 的实际判断还包括：

- 连续字符需要处在非字母数字边界中；
- 换行或空格包裹的块需要至少 64 个 hex 字符；
- wrapped 块的 hex 比例约为 0.95 以上；
- 至少有一行达到 64 个 hex 字符。

**注意“64 位”不是 64 bytes**

代码按 Python 字符串长度和字符 offset 判断。ASCII hex 中一个字符通常对应一个 UTF-8 byte，但这个规则的单位仍然是 Unicode 字符，不应写成“64 字节保证是乱码”。

**边界和风险**

~~~text
deadbeef...（短串）             -> 保留
value=0xabab...（普通代码常量） -> 边界保护通常保留
hash=abcdef...（恰好 64 hex）   -> 当前可能误命中
日志/URL/数据库 ID              -> 需要上下文保护
~~~

源码注释中出现“保留 hash”的意图，但当前实现没有完整的通用 hash 白名单；合法的 64 位 hash、对象 ID、URL 参数仍可能满足形状。接手人不能把 `hex_blob` 描述为“已经可靠区分 hash 和 dump”。V7 应优先加入上下文保护和 `keep_hash` 配置，并用真实 hash 反例验收。

### 10.14 异常空格、Tab、行尾空格和空行

这些问题来源于 PDF/OCR、网页复制、全角排版和表格导出，不能用一个“所有空白都替换”的正则解决。

| 算子 | 处理对象 | 动作 |
|---|---|---|
| `abnormal_space` | U+00A0、U+2003、U+3000 | 换成普通空格 |
| `abnormal_space` | U+202F、U+2009 | 删除 |
| `line_boundary_space` | 换行前和文本末尾的空格/Tab | 删除 |
| `blank_line_collapse` | 连续三个及以上 LF | 最多保留两个 LF |

例子：

~~~text
A<U+3000>B          -> A B
上一行<space>\n下一行 -> 上一行\n下一行
A\n<space>\nB        -> A\n\nB
A\n\n\nB          -> A\n\nB
~~~

当前不会：

- 全局把多个普通空格压成一个；
- 把正文中间的 Tab 全部改成空格；
- 把所有换行合成一行；
- 因为空白异常就删除整条记录。

`line_boundary_space` 和 `blank_line_collapse` 依赖前面规则产生的“有效输出单元”扫描，所以它们必须放在清洗链后段；例如 U+2029 先转成两个 LF，随后才参与空行判断。

### 10.15 合法重复、可疑重复和阈值 3/4/5/8

项目里“threshold 是 3 吗”的疑问来自不同阶段使用了不同数字。正确对应关系如下：

| 数字 | 所在位置 | 真实含义 |
|---:|---|---|
| 3 | `text_sub_dedup` 短片段正则候选 | 连续三次先进入候选，不代表最终报告 |
| 4 | 清洗侧 `math_symbol_flood` | 同一数学/符号字符连续四次就删除 |
| 5 | `text_sub_dedup` 最终标准、`repeated_noise_token` | 通用连续重复或窄 token 重复达到五次 |
| 8 | QC 侧 `math_symbol_flood` | 同一数学/符号字符连续八次才报告 |

**为什么普通 dedup 只报告**

下面的重复都可能是合法内容：

~~~text
诗歌/歌词：      回来，回来，回来……
教材：            表示。表示。表示。表示。表示。
课堂指令：        “向我走1步”重复多次
枚举/列表：       one, two, three ...
拟声词：          哈哈哈哈哈、嘎嘎嘎
表格/棋谱：       右 左 右左 右 左右 左
~~~

所以 `text_sub_dedup` 输出的是建议区间，不是自动删除命令。V6 唯一的自动重复清洗例外是窄规则 `repeated_noise_token`：要求 ASCII 字母数字 token 同时含字母和数字、内部有至少四个连续相同字符、连续出现至少五次，并且不在 Markdown fenced code 中。

**“正确的重复”怎么判断**

不能只看次数。人工复核至少要看：

1. 重复单元是否跨完整句子/段落，而不是从单词中间截出来；
2. 前后是否有标题、表格、歌词、课堂说明或题目结构；
3. 重复是否保留了递进数字、变量或不同实体；
4. 该数据源过去是否经常产生同类抽取重复；
5. 删除后是否会破坏原文的教学、文学或格式语义。

### 10.16 `clean_article_comment.py`：为什么要作为独立前置阶段

同事提供的 `clean_article_comment.py` 不是 Unicode 字符清洗器，而是文章/评论结构预处理脚本。它做两件事：

#### `strip_truncated_titles(content)`

匹配 `<|start_of_articleid=N|>` 后紧跟、以 Unicode 省略号 `…` 结尾的标题行，删除截断标题文本但保留 article marker。

当前只匹配字符 `…`，不匹配三个 ASCII 点 `...`。这是已知边界。

#### `tag_comment_quotes(content)`

在 `<|start_of_comment_articleid=N|>` 和结束 marker 之间：

- 按空行 `\n\n` 分割评论；
- 当前段落至少 40 个字符、至少 8 个单词时才进入相似度比较；
- 与前面评论的 `difflib.SequenceMatcher` 相似度达到 0.85 时，包成 `<ref id=N>...</ref>`；
- 纯 URL 和部分编辑系统样板不作为可引用正文；
- 连续命中的段落会合并，紧邻的 `said:`/`wrote:` 抬头也可能被并入。

推荐链路：

~~~text
原始 article/comment JSONL
    -> clean_article_comment.py（结构处理）
    -> quality_checks_V6（字符和片段清洗）
    -> QC（乱码、重复、dedup）
~~~

它必须保持独立，原因是结构标记和字符噪声的误报边界不同。脚本默认只 demo，不写盘；只有 `--run` 才处理文件。正式运行前必须先查看 demo，确认 `…` 标题和 `<ref>` 标记没有误包。

### 10.17 V4/V5/V6 比较，以及“先标记再清洗”到底是什么

用户曾问过是否可以先把 V4 跑一遍，再用当前脚本再跑一遍。可以用于对照实验，但不应把多个版本无记录地串成生产链路。每个版本的规则集合、优先级和 metadata schema 可能不同，串跑会让最终 offset 难以解释。

当前推荐的逻辑是：

~~~text
扫描原始字符串
    -> 在 replacements/命中表中记录规则、原始 offset、replacement
    -> 根据命中表生成 cleaned_text
    -> 用同一份命中表构造 meta.cleaning_v6
    -> 对 cleaned_text 再跑独立 QC
~~~

这就是“先标记，再根据标记清洗”的内部实现。不是先把原文改掉，再从改后的字符串猜哪里发生过变化。好处是：

- metadata 的 offset 仍指向原始字段；
- 多个算子命中同一字符时可以合并原因；
- span 可以按规则和连续位置聚合；
- 输出可以复核和回滚。

需要区分三种“标记”：

| 标记 | 由谁产生 | 是否改正文 |
|---|---|---|
| `replacements` | 清洗器内部 | 随后会应用到正文 |
| `meta.cleaning_v6` | 清洗器输出的溯源记录 | 不再改正文 |
| `issues/highlights` | QC 检测器 | 只报告，不改正文 |

V6 清洗后再 QC 的意义，是检验问题是否减少，而不是证明所有 QC 命中都应该被删除。

### 10.18 Excel、`variation_context` 和可视化定位

早期输入是质检 Excel，重点字段包括 `variation_context`，用户曾特别查看 AW 列第 10 行的 `▪【️】`。XLSX 模式按第一行表头找字段：

~~~bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.xlsx \
  --output output.v6.cleaned.xlsx \
  --format xlsx \
  --fields variation_context \
  --sheet Sheet1 \
  --summary output.v6.summary.json
~~~

这里的 `--fields` 是表头名称，不是 Excel 列号；如果表头不叫 `variation_context`，命令会报字段不存在。XLSX 清洗会写新文件，不覆盖源文件；XLSX 模式本身不附加 JSONL 的 `meta.cleaning_v6`，如需完整溯源，建议同时导出 JSONL 或 summary。

**可视化的 `hank you` 案例**

质检 Excel 曾显示标注上下文是 `hank you`，但原文是 `thank you`。根因不是清洗算子删掉了 `t`，而是旧可视化代码 `_find_repetition_offset`：

1. repetition checker 只返回可读的 `reason` 字符串；
2. 可视化代码从 reason 解析 top n-gram/token；
3. 再对原文调用 `text.find(candidate)`；
4. candidate 可能从重复片段中间开始，或被去掉边界空格；
5. Excel 因而从第二个字符开始包高亮。

修复原则：检测器应直接返回结构化绝对 offset；可视化必须使用 detector 给出的 offset，并在输出前断言 `text[start:end]` 与命中内容一致。看到首字母缺失时，应先检查 offset、切片和编码，不要先修改清洗规则。

### 10.19 V6 清洗后仍不合格的 17 条：这不是清洗失败的同义词

在 800 条 `sample_100_per_domain.jsonl` 上，V6 清洗后 QC 结果是：

| 结果 | 数量 |
|---|---:|
| 总记录 | 800 |
| QC 通过 | 783 |
| QC 失败 | 17 |
| `sub_dedup_continuous` | 16 |
| `mojibake` | 1 |

16 条连续重复主要包含：

- `表示。` 连续出现五次；
- `orange,`、`over and`、`on and`、`too` 等英文短片段；
- `one, two, three` 课堂式枚举；
- `哈哈`、`朝`、`长`、`嘎` 等拟声或短字重复；
- 题库/教材中的“测验常识判断题”重复；
- “向我走1步”课堂指令重复十次。

这些结果需要按来源和上下文判断，不能把 16 条整体删除。1 条 `mojibake` 是英文课程文本中的三个 C1 控制字符：U+0091、U+0093、U+0094。它们能被 QC 找到，但 V6 清洗器没有 `c1_control` 清洗规则，因此仍保留并报告。这说明：

~~~text
QC 找到问题 != 清洗器已经知道如何安全修复问题
~~~

C1 字符如果要清理，最好回到原始字节或来源文件确认；单纯删除可能掩盖原本丢失的引号、数字或词。

---

## 11. `meta.cleaning_v6` 溯源信息详解

### 11.1 推荐的 compact span 结构

生产推荐使用：

~~~bash
--add-cleaning-meta --meta-detail spans
~~~

一条含有多个清洗片段的记录大致如下：

~~~json
{
  "meta": {
    "cleaning_v6": {
      "version": "v6.0",
      "schema": "compact_by_rule_v1",
      "line_no": 184,
      "changed": true,
      "fields": ["content"],
      "rules": ["variation_selector", "emoji_all", "styled_math_alnum"],
      "changed_fields": ["content"],
      "change_count": 6,
      "rule_counts": {
        "emoji_all": 3,
        "styled_math_alnum": 1,
        "line_boundary_space": 2
      },
      "field_counts": {"content": 6},
      "by_rule": {
        "emoji_all": {
          "count": 3,
          "field_counts": {"content": 3},
          "span_count": 1,
          "spans_truncated": false,
          "spans": [
            {
              "field": "content",
              "start": 220,
              "end": 223,
              "count": 3,
              "action": "delete",
              "original_preview": "🏳‍🌈",
              "replacement_preview": "",
              "codepoint_counts": {
                "U+1F3F3": 1,
                "U+200D": 1,
                "U+1F308": 1
              },
              "reasons": ["matched"]
            }
          ]
        }
      }
    }
  }
}
~~~

### 11.2 字段含义

| 字段 | 含义 |
|---|---|
| `version` | 清洗器版本，目前 `v6.0` |
| `schema` | metadata 格式版本，目前 `compact_by_rule_v1` |
| `line_no` | JSONL 物理行号，从 1 开始；不是业务主键 |
| `changed` | 本条指定字段是否发生变化 |
| `fields` | 本次允许扫描的字段列表 |
| `rules` | 本次启用的规则列表，按配置顺序记录 |
| `changed_fields` | 实际有变更的字段 |
| `change_count` | 字符/替换单元变更数，不是 token 数、span 数或记录数 |
| `rule_counts` | 每个规则的字符/替换单元计数 |
| `field_counts` | 每个字段的字符/替换单元计数 |
| `by_rule` | 按规则聚合的连续原文 span |
| `span_count` | 该规则的原始连续 span 数 |
| `spans_truncated` | 是否因为 `--max-meta-spans` 截断 span 列表 |
| `start/end` | 原始字段中的字符区间，start 包含、end 不包含 |
| `original_preview` | 原始命中片段的预览，过长时截头尾 |
| `replacement_preview` | 对应替换内容；删除时为空串 |
| `codepoint_counts` | 该 span 内各码点的计数 |
| `reasons` | 该 span 内规则原因的去重集合 |

### 11.3 offset 的单位和使用限制

V6 的 offset 是 Python `str` 的 Unicode code-point 索引：

- 不是 UTF-8 byte offset；
- 不是 UTF-16 code-unit offset；
- 对 emoji 等非 BMP 字符，在 Python 3 中通常按一个 code point 计数；
- 必须在原始字段上切片，不能拿清洗后的文本直接复用同一 offset。

例如：

~~~python
start, end = span["start"], span["end"]
original_piece = record["content"][start:end]
~~~

清洗会改变长度，所以如果下游要在 cleaned 文本上继续定位，必须重新计算 offset，不能把原文 offset 当作新文本 offset。

### 11.4 `count` 为什么可能是 55

测试案例中五个 `a111111111` 及其分隔空格会形成一个 `repeated_noise_token` span，metadata 可能是：

~~~text
rule_counts.repeated_noise_token = 55
~~~

这表示被替换/删除的字符或替换单元数量，不表示发现了 55 个 token。若需要 token 数，应在规则原因或未来 schema 中单独增加 `unit_count`/`token_count`，不要从 `count` 猜。

### 11.5 `meta-detail` 三种模式

| 模式 | 内容 | 推荐 |
|---|---|---|
| `spans` | 按规则聚合连续区间，默认 | 生产默认，体积较小 |
| `chars` | 每个原始字符的详细码点和 replacement | 调试、误删分析 |
| `both` | spans + 字符级变化 | 小样本审计，不建议大规模默认 |

`--max-meta-spans 0` 表示每条记录不限制 span 数；正数会截断每个规则的 span 列表但仍保留总 `span_count` 和 `spans_truncated=true`。`--max-meta-changes` 只限制 `chars/both` 的详细 changes，不影响正文清洗，也不影响 `spans` 聚合。

### 11.6 已有 `meta` 字段时的行为

- `meta` 不存在或为 `null`：创建对象并写入 `meta.cleaning_v6`；
- `meta` 已经是 dict：在原对象中增加/更新 `cleaning_v6`；
- `meta` 是字符串、列表等非 dict：不覆盖原值，改写到顶层 `_cleaning_v6`。

这项保护是为了避免清洗 provenance 覆盖源数据。接手人读取 metadata 时应同时检查：

~~~python
record.get("meta", {}).get("cleaning_v6")
record.get("_cleaning_v6")
~~~

### 11.7 metadata 的边界

当前 metadata 能回答“哪条规则在原文哪个位置做了什么”，但不能回答：

- 清洗前原始文件的 byte offset；
- PUA 显示字体是什么；
- HTML entity 原本想表达哪个标点；
- 复杂重复是否应该删除；
- QC 结果对应的模型 token offset。

这些需要上游文件信息、字体信息或独立 QC 结构化 offset 支持。

## 12. 端到端决策表：什么自动改，什么只报告

| 问题类型 | 典型例子 | 当前处理 | 是否自动改 | 推荐人工动作 |
|---|---|---|---|---|
| 明确 variation selector | U+FE0F、U+E0100 | `variation_selector` | 是，删 selector | 抽样确认异体字数据需求 |
| 数学样式字母 | `𝙤` | `styled_math_alnum` | 是，NFKC 转普通字母 | 检查公式字段 |
| 明确不可见控制 | BOM、WORD JOINER、SOFT HYPHEN | `control_invisible` | 是 | 检查是否来自上游编码污染 |
| 明确装饰符号 | 分隔线、块元素 | `decorative_symbol` | 是 | 检查表单/列表语义 |
| emoji | `🏳‍🌈`、`1️⃣` | `emoji_all` | 是，当前 strict 策略 | 多语种场景评估模式 |
| 已知 PUA 噪声 | U+F8FF/U+F04A | `pua_known_noise` | 是 | 维护来源登记 |
| 上下文明确 PUA | 行首 U+F0B7、数字范围 U+F07E | `pua_contextual` | 是，有限替换 | 新码点先建立正反例 |
| 未知 PUA | 未登记私用码位 | `pua` QC | 否 | 保留、查字体/数据源 |
| 长 hex dump | >=64 hex | `hex_blob` | 是，存在 hash 风险 | 复核代码/URL/ID |
| HTML/训练 token | `<br>`、`<|im_end|>` | `forbidden_strings`/`html_entity` | 是 | 若标点有意义，评估解码模式 |
| 可疑 token 洪泛 | `a111111111` x5 | `repeated_noise_token` | 是，窄条件 | 检查业务 ID/代码块反例 |
| 普通连续重复 | `表示。` x5、歌词 | `text_sub_dedup` | 否 | 看上下文和来源 |
| n-gram/token flood | assistant 输出循环 | `repetition_check` | 否 | 人工裁剪或剔除回答部分 |
| FFFD/C1/经典乱码 | `�`、U+0091、`æ–‡` | `mojibake_detect` | 否 | 回原始字节或单独修复 |
| 文章截断标题/引用 | `…` 标题、评论复述 | `clean_article_comment.py` | 是，独立结构阶段 | demo 后抽样 |

核心决策可以压缩成：

~~~text
高置信度、字符级、可解释       -> 自动清洗 + meta
有语义替换但上下文足够明确       -> 条件替换 + meta
需要编码恢复/语义判断/合法重复    -> QC 报告 + 人工复核
~~~

## 13. 验证基线、速度和可复现结果

### 13.1 800 条 `sample_100_per_domain` 基线

文件规模：800 条，UTF-8 JSONL 大小 64,559,603 bytes。V6 配置为只清洗顶层 `content`、开启 `meta_detail=spans`，没有删除任何记录。

清洗统计：

| 指标 | 数值 |
|---|---:|
| 输入/输出记录数 | 800 / 800 |
| `content` 发生变化的记录 | 537 |
| 字符/替换单元变化总数 | 45,526 |
| 带 metadata 的记录 | 800 |

主要清洗命中：

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

清洗前/后 QC：

| 阶段 | 通过 | 失败 | 主要原因 | 用时 |
|---|---:|---:|---|---:|
| 原始文本 | 422 | 378 | `mojibake` 367，dedup 16 | 348.280 s |
| V6 清洗后 | 783 | 17 | `mojibake` 1，dedup 16 | 345.423 s |

这次 QC 使用 `thread`、2 workers。粗略速度约为 2.30-2.32 records/s，按完整 JSONL 文件大小折算约 185-187 KB/s。这个数字是当前机器、当前数据和 QC 配置的实测值，不是算法固定性能；清洗写盘时间与 QC 时间要分开记录。

### 13.2 其他文件的规模

| 文件 | 记录数 | UTF-8 文件大小 |
|---|---:|---:|
| `mrcr_merged_sample_1000.jsonl` | 1,000 | 781,570,085 bytes |
| `retrieval_sample_500.jsonl` | 500 | 374,198,030 bytes |

MRCR 的记录更长，不能用 800 条样本的 records/s 直接推算全量时间。应在目标机器上先跑 1%、记录实际字节吞吐和内存，再估算全量。

### 13.3 基线产物

最近一次可复核产物在：

~~~text
handoff_v6_validation_20260825/
  clean_summary.json
  sample_100_per_domain.v6.cleaned.jsonl
  qc/before/summary.json
  qc/before/issues.jsonl
  qc/before/failed_records.jsonl
  qc/after/summary.json
  qc/after/issues.jsonl
  qc/after/failed_records.jsonl
  README.md
~~~

其中 issues/failed_records 可能包含内部正文，不应上传公共仓库或随意发给第三方。

## 14. 复现命令和交付方式

下面命令均以 `quality_checks_V6` 所在目录为当前工作目录；给别人时要把路径替换为对方机器上的项目根目录。

### 14.1 安装依赖

~~~bash
python3 -m pip install -r quality_checks_V6/requirements.txt
~~~

依赖只有：

- `numpy>=1.20`：mojibake QC；
- `openpyxl>=3.0`：XLSX 清洗；
- 字符清洗和 JSONL 主流程使用 Python 标准库。

### 14.2 最小 smoke test

~~~bash
python3 -m py_compile \
  quality_checks_V6/clean_variation_chars.py \
  quality_checks_V6/quality_api.py \
  quality_checks_V6/run_long_dialog_quality_checks.py \
  quality_checks_V6/mojibake_detect/detector.py \
  quality_checks_V6/repetition_check/loop.py \
  quality_checks_V6/text_sub_dedup/continuous_dedup_mapper_v7.py

python3 -m unittest quality_checks_V6.tests.test_repeated_noise_token
~~~

测试覆盖 `a111111111` 五次删除、四次保留、普通词/年份/ID 保留、代码块保留、hex 边界和 compact metadata。

### 14.3 JSONL 清洗

~~~bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.jsonl \
  --output /path/to/output.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --max-in-flight 32 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /path/to/output.v6.clean_summary.json
~~~

`--output` 必须不同于 `--input`。超长记录或内存有限时降低 workers 和 max-in-flight。大文件不要先通过 HTTP API 逐条传输，优先使用 CLI。

### 14.4 清洗前后 QC

清洗后单独 QC：

~~~bash
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input /path/to/output.v6.cleaned.jsonl \
  --output-dir /path/to/qc.after \
  --checks all \
  --workers 16 \
  --executor process \
  --no-passed-output \
  --failed-records-output /path/to/qc.after/failed_records.jsonl
~~~

也可以让清洗器自动跑 `--qc before`、`--qc after` 或 `--qc both`。在某些受限 macOS 环境中，多进程 semaphore 可能不可用，可改成：

~~~bash
--workers 2 --executor thread
~~~

这是执行器差异，不改变规则语义。

### 14.5 XLSX 清洗

~~~bash
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.xlsx \
  --output /path/to/output.v6.cleaned.xlsx \
  --format xlsx \
  --fields variation_context \
  --sheet Sheet1 \
  --summary /path/to/output.v6.summary.json
~~~

先确认表头和工作表名称。XLSX 模式没有 JSONL per-record metadata；summary 只包含统计和有限样例。

### 14.6 Python API

~~~python
from quality_checks_V6 import clean_record, check_record, clean_and_check

source = {"content": "𝙤 🏳‍🌈 a111111111 a111111111 a111111111 a111111111 a111111111"}

cleaned = clean_record(source, fields=["content"], add_meta=True)
print(cleaned["record"])
print(cleaned["cleaning"])

quality = check_record(cleaned["record"], checks=["mojibake", "repetition", "dedup"])
print(quality["failed"], quality["issues"])

both = clean_and_check(source)
~~~

API 会深拷贝输入 record，不应修改调用方原对象。默认 fields 是 `content`，默认 metadata 是开启的 spans 模式。

### 14.7 HTTP API

启动：

~~~bash
python3 quality_checks_V6/quality_api.py --host 127.0.0.1 --port 8000
~~~

调用：

~~~bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/rules
curl -s -X POST http://127.0.0.1:8000/v1/clean \
  -H 'Content-Type: application/json' \
  -d '{"record":{"content":"𝙤 🏳‍🌈"},"fields":["content"]}'
~~~

`/v1/clean`、`/v1/check`、`/v1/clean-and-check` 是单条 JSON record 接口，默认请求体上限 50 MiB；没有认证和 TLS，不要直接暴露公网。别人调用接口时，实际是请求服务所在的服务器执行清洗；如果对方只拿到代码包，也可以直接在自己的服务器用 Python/CLI 跑。

## 15. 开源库是否应该采纳

开源库适合做候选检测、规范化和结构识别，但不应该未经回归就替换当前策略。

| 库/标准库 | 可帮助的部分 | 建议定位 |
|---|---|---|
| `unicodedata`（标准库） | 名称、类别、NFKC/NFD、码点分类 | 继续作为底层基础 |
| `ftfy` | 候选 mojibake 修复、文本修复启发式 | 离线候选修复；输出 diff/置信度，先不自动覆盖 |
| `regex` | Unicode 属性、grapheme cluster `\\X` | V7 emoji/用户感知字符分组候选 |
| `emoji` | Emoji 属性和标准序列识别 | 替代宽范围 emoji 区间的候选检测；需锁版本 |
| `html.unescape` | HTML entity 解码 | 新增可选 `html_unescape` 模式，不改变当前删除语义 |
| `datasketch`/SimHash | 文档级近重复、跨记录相似 | 另建 document-dedup 阶段，不塞进字符清洗器 |
| `rapidfuzz` | 评论引用/近似字符串匹配 | 可评估替代 `difflib`，需保留阈值和样例 |

**推荐的采纳顺序**

1. 先用 `regex`/`emoji` 在离线评估中对比当前 `emoji_all` 的命中差异；
2. 用 `ftfy` 对 mojibake 输出候选 before/after，不直接写回训练文件；
3. 用 `html.unescape` 做另一套保留标点的实验模式；
4. 用 SimHash/MinHash 处理文档级近重复，和连续片段 dedup 分开；
5. 只有在误报率、召回率和运行开销都经过样本验证后，才把库接入默认链路。

原因是：开源库通常面向通用文本质量或显示效果，而当前项目还有业务约束：不删整条记录、需要原始 offset、字段范围有限、emoji 业务取舍激进、PUA 映射依赖数据源。库可以增强算子，但不能替代业务决策。

## 16. V7 建议开发顺序和风险清单

### P0：冻结 V6

- 保留 `quality_checks_V6` 原目录和压缩包，不在原目录无记录覆盖；
- 保存 V6 源码 checksum、规则列表、800 条 summary；
- 建立 `quality_checks_V7` 独立目录，统一 `v7.0`、`cleaning_v7` 和 API service name；
- 用同一批输入跑 V6/V7 对照，记录 changed rows、rule counts、QC before/after。

### P1：先修定位，再扩规则

- repetition checker 直接返回结构化绝对 offset，删除 `_find_repetition_offset` 的 reason 反解析依赖；
- dedup 结果统一合并重叠 span，避免长文本切片重复返回；
- 所有可视化在渲染前验证 `text[start:end]`；
- 将“物理字符数、span 数、token 数、记录数”拆成不同字段。

### P1：建立模式和保护清单

- `strict_training` / `language_safe` / `symbol_preserving` 三种配置；
- emoji 按 grapheme cluster/标准 Emoji 属性识别；
- ZWJ、ZWNJ 与 emoji 附件分开记录；
- PUA 映射表带数据源、正反例和版本；
- `hex_blob` 增加 hash、URL、JSON、代码块和数据库 ID 的上下文保护；
- C1/FFFD/经典 mojibake 建立“候选修复”输出，而不是盲删。

### P1：降低重复误报

- 保留 `repeated_noise_token` 的窄条件；
- 为教材、歌词、表格、棋谱、课堂指令和题库增加结构特征；
- `char_len` 很小的 dedup 结果默认降级 warning；
- 增加完整词边界和句子边界约束；
- 可选加入文档级近重复，但不要和连续片段规则混称。

### P2：metadata schema v2

建议新增但不要破坏 V6 字段：

~~~json
{
  "unit_count": 55,
  "span_count": 1,
  "token_count": 5,
  "offset_unit": "unicode_codepoint",
  "source_digest": "...",
  "mode": "strict_training"
}
~~~

其中 `token_count` 只有在规则确实按 token 识别时才填；不能把字符数硬改名成 token 数。

### 风险清单

| 风险 | 当前状态 | V7 处理优先级 |
|---|---|---:|
| emoji 宽范围误删语义符号 | 已知 | P1 |
| strict 模式影响小语种 ZWJ/ZWNJ | 已知且有业务取舍 | P1 |
| 64 hex 误删合法 hash/ID | 已知 | P1 |
| unknown PUA 无法自动恢复 | 有意保留 | P1 |
| FFFD/C1 删除后无法恢复原字 | 当前只报告 | P1 |
| dedup 误报教材/歌词/表格 | 已知 | P1 |
| repetition 高亮 offset 偏移 | 旧代码已暴露 | P0 |
| `meta_private_info` 未递归清洗 | 当前设计 | 由字段策略决定 |
| 大文件多进程内存/sem | 环境相关 | P2 |
| API 无认证/TLS | 当前设计 | 部署层处理 |

## 17. 最小验收清单

每次 V7 或规则变更至少完成：

- [ ] 输入与输出路径不同，原始 JSONL/XLSX 未被覆盖；
- [ ] `py_compile` 通过；
- [ ] 单元测试通过；
- [ ] `/health` 版本与目录一致；
- [ ] `/v1/rules` 与 README/代码默认规则一致；
- [ ] 前后记录数一致，没有隐式删除整条记录；
- [ ] metadata 的版本、schema、字段范围正确；
- [ ] offset 明确标注为原始 Unicode 字符 offset；
- [ ] 每个新算子至少有一个正例和三个误删保护例；
- [ ] 有 emoji 复合序列测试；
- [ ] 有阿拉伯文、波斯文、印地文、希伯来文和东南亚文字样例；
- [ ] 有 LaTeX、HTML、URL、代码块和合法 hash 样例；
- [ ] 有 PUA 正例、反例和未知码点样例；
- [ ] 有教材、歌词、表格、课堂口令等合法重复样例；
- [ ] 清洗前后都跑 QC，并保存 summary/issues/failed records；
- [ ] 800 条基线的变化量和通过率没有未经解释的跳变；
- [ ] 大文件记录了 records/s、UTF-8 bytes/s 和内存峰值；
- [ ] 发布包排除了内部正文、上下文明细和密钥；
- [ ] 新包生成 checksum 和变更说明。

## 18. 给下一位维护者的文件索引

以下路径相对于项目根目录：

| 文件 | 作用 |
|---|---|
| `quality_checks_V6/clean_variation_chars.py` | 字符/片段清洗主链路 |
| `quality_checks_V6/run_long_dialog_quality_checks.py` | 批量 QC 入口、输出 issues/summary |
| `quality_checks_V6/quality_api.py` | Python API 和 HTTP API |
| `quality_checks_V6/mojibake_detect/detector.py` | 编码异常检测器 |
| `quality_checks_V6/mojibake_detect/custom_patterns.py` | 乱码/双重编码/HTML 模式 |
| `quality_checks_V6/mojibake_detect/utils.py` | Unicode 集合和分类辅助函数 |
| `quality_checks_V6/repetition_check/loop.py` | n-gram、token flood、枚举检测 |
| `quality_checks_V6/text_sub_dedup/continuous_dedup_mapper_v7.py` | 连续片段重复检测；文件名中的 v7 是历史命名 |
| `quality_checks_V6/text_sub_dedup/text_utils.py` | 分词、去标点、offset map、代码识别 |
| `quality_checks_V6/tests/test_repeated_noise_token.py` | V6 重复噪声回归测试 |
| `clean_article_comment.py` | 文章标题截断和评论引用前置处理 |
| `content_checker.py` | 外部 Aegis/EB5 生产 QC 插件，不是 V6 清洗器 |
| `error_strings_checker.py` | 外部特殊字符串 QC 插件 |
| `handoff_v6_validation_20260825/` | 800 条 V6 基线摘要和验证产物 |
| `QUALITY_CHECKS_V7_HANDOFF.md` | 较短的交接摘要；本文件是完整案例/算子说明 |

### 18.1 接手工作的第一轮动作

接手人建议按以下顺序执行：

1. 阅读本文件和 `quality_checks_V6/README.md`；
2. 在自己的环境执行 smoke test；
3. 用 20 条或 1% 数据做 dry-run，确认字段和输出目录；
4. 用 800 条基线重跑 V6，比较 summary；
5. 抽看 `▪【️】`、`𝙤`、`a111111111`、PUA、emoji、64 hex、LaTeX 和合法重复；
6. 再决定 V7 先修 offset、hex 保护还是语言安全模式；
7. 新规则必须在 V7 独立目录和 changelog 中提交，不直接改 V6 生产基线。

### 18.2 最终原则

~~~text
不要以“命中更多”为成功标准。
成功标准是：每次修改都能解释、定位、复现、回滚，
并且知道哪些问题仍然只被报告、尚未被安全修复。
~~~
