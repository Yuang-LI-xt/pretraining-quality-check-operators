# V5 算子总览：从 V3 到 V5 的升级

这份文档用于内部说明 `quality_checks_V5` 的完整算子体系，重点回答三个问题：

1. V5 相比 V3 到底增强了什么
2. 每个清洗 / 检测算子具体做什么
3. 每个算子对应什么典型 case

---

## 一句话总结

V3 主要还是“少量字符修复 + 组合符/emoji 清理”；V5 已经升级成“字符规范化 + 噪声删除 + 版式空白治理 + 质量检测”的完整工具链。

V5 的核心变化是：

- 从少数规则，扩展到 PUA、双向控制符、HTML 实体、不可见控制符、符号洪泛、边界空白等更完整的清洗面
- 从“粗删”变成“更保守、更语义友好”的修复
- 从只清洗，扩展到清洗前后都可做 QC
- 对多语种、数学公式、表单符号、LaTeX 结构的破坏明显更少

---

## V3 -> V5 主要变化

| 维度 | V3 | V5 |
|---|---|---|
| `variation_selector` | 删除 selector，且会连前一个 base 一起删 | 只删 selector；只有在 `emoji_all` 语境下才会按 emoji 序列联动处理 |
| `styled_math_alnum` | 直接删除数学样式字母数字 | 先做 NFKC 规范化，尽量保留可读性 |
| `combining_decoration` | 默认启用，按 base + 连续 combining 处理 | 不再把它当默认主规则，只保留极少量白名单组合符删除 |
| `exotic_combining` | 默认启用，直接删 | 变成 legacy 兼容项，不在默认路径里 |
| `emoji_all` | 删除 emoji / 连接符 / keycap 等 | 继续保留，但更注意语义符号白名单 |
| 新增清洗面 | 基本没有 | `bidi_control`、`pua_contextual`、`pua_known_noise`、`decorative_symbol`、`decorative_combining`、`control_invisible`、`line_separator`、`html_entity`、`forbidden_strings`、`math_symbol_flood`、`line_boundary_space`、`blank_line_collapse` |
| 结构保护 | 弱 | 更强，尤其是表单符号、LaTeX、中文多语种正文 |

---

## 一、V5 清洗算子

### 1. `variation_selector`

功能：
- 删除 Unicode variation selector
- 主要处理 `U+FE00-U+FE0F`、`U+E0100-U+E01EF`

和 V3 的区别：
- V3 会连前一个 base 一起删，破坏性更强
- V5 默认只删 selector 本身

典型 case：

```text
汉 + U+E0100(variation selector) -> 汉
* + U+FE0F(emoji variation selector) -> *
```

说明：
- 这类字符本质是字形选择符，不该把前面的字也删掉
- 如果是 emoji variation sequence，`emoji_all` 会接管整段删除

---

### 2. `styled_math_alnum`

功能：
- 处理数学样式字母数字，如黑板体、花体、斜体数学字形
- V5 不再直接删，而是先做 NFKC 规范化

典型 case：

```text
𝙜𝙤𝙞𝙣𝙜 -> going
𝓥 -> V
```

说明：
- 比起粗删，这样更能保留原意

---

### 3. `abnormal_space`

功能：
- 把异常空格统一成普通空格，或直接删除
- 覆盖 `NBSP`、`EM SPACE`、全角空格、窄 NBSP、thin space

典型 case：

```text
A　B -> A B
A B -> AB
```

说明：
- 这类空格常见于 PDF/OCR 或粘贴污染

---

### 4. `bidi_control`

功能：
- 删除双向文本控制符
- 覆盖 embedding / override / isolate / LRM / RLM 等

典型 case：

```text
‭John‬ 10:7 -> John 10:7
```

说明：
- 这类字符对训练基本没价值，但会影响显示顺序

---

### 5. `pua_known_noise`

功能：
- 精确删除已知的私用区噪声字符
- 当前默认包括：
  - `U+F8FF` Apple logo
  - `U+F04A` 字体私有笑脸/噪声

典型 case：

```text
 -> 
```

说明：
- 这是“已知坏点精确删除”，不是全 PUA 删除

---

### 6. `pua_contextual`

功能：
- 只在高置信上下文下修复 / 删除部分 PUA 字符

已知修复：

- `U+F0B7`：列表项 bullet，修成 `- `
- `U+F07E`：数值区间，修成 `~`
- `U+E010`：点号修复，修成 `．`
- `U+E011`：专名连字符，修成 `-`
- `U+E10B`：专名撇号，修成 `'`
- `U+E11A/U+E11B/U+E11C`：脚注标记，直接删
- `U+E618`：bullet，按上下文修成 `-` 或 `\n-`
- `U+E1BD`：个别 ebook 误码，修成 `约`

典型 case：

```text
 3-5 -> 3~5
 item -> - item
```

说明：
- 这是 V5 里很重要的安全修复位
- 不是把整个 PUA 区域一刀切删掉

---

### 7. `decorative_symbol`

功能：
- 删除装饰性符号
- 覆盖：
  - Box Drawing
  - Block Elements
  - Geometric Shapes
  - Misc Symbols
  - Dingbats

但保留语义表单符号：

- `□`
- `☐`
- `☑`
- `☒`
- `✓`
- `✗`
- `○`
- `●`

典型 case：

```text
▒▓██╯ -> 
□ 选项A -> □ 选项A
```

说明：
- 这是 V5 很关键的一次“去装饰、保语义”
- 比 V3 的大范围 emoji/装饰删除更稳

---

### 8. `decorative_combining`

功能：
- 只删除 V5 白名单里的装饰组合符
- 具体为：
  - `U+0336`
  - `U+033F`
  - `U+035C-U+0361`

典型 case：

```text
s̶t̶r̶i̶k̶e̶ -> strike
( ͡° ͜ʖ ͡°) -> ( ° ʖ °)
```

说明：
- V5 明确避免按 `Mn/Me` 类别一刀切
- 这样不会误伤希伯来语、阿拉伯语、印地语等正常组合符

---

### 9. `emoji_all`

功能：
- 删除 emoji 及其附件字符
- 覆盖：
  - emoji 本体
  - ZWJ 序列
  - keycap 序列
  - enclosing / attachment 类字符

典型 case：

```text
😂 -> 
🏳‍🌈 -> 
1️⃣ -> 
```

说明：
- 表情、装饰性图标、键帽 emoji 统一视为噪声

---

### 10. `control_invisible`

功能：
- 删除不可见控制垃圾字符
- 覆盖：
  - BOM
  - zero-width 系列
  - word joiner
  - soft hyphen
  - 相关方向标记 / 连接字符

典型 case：

```text
a​b -> ab
```

说明：
- 这类字符通常看不见，但会污染训练和检索

---

### 11. `line_separator`

功能：
- 把行分隔符规范化成换行
- 规则：
  - `U+2028 -> \n`
  - `U+2029 -> \n\n`

典型 case：

```text
第一段\u2028第二段 -> 第一段
第二段
```

说明：
- 这是结构修复，不是简单删除

---

### 12. `html_entity`

功能：
- 删除整个 HTML 实体串
- 覆盖命名实体、十进制实体、十六进制实体

典型 case：

```text
&gt; -> 
&#123; -> 
&#x1F; -> 
```

说明：
- 很多 `&gt;` 来自引用符号转义
- 如果想保语义，可以后续另做 `html.unescape` 版本

---

### 13. `forbidden_strings`

功能：
- 删除 HTML 残留、训练 token、以及独立的字面量转义错误

目前重点包括：

- `<br>` / `<|im_start|>` / `<|endoftext|>` 一类残留
- 独立的字面量 `\n` / `\t` / `\r`

V5 修复后的关键边界：

- `\times` 不会再被误切成 `imes`
- `\theta`、`\right`、`\r{a}`、`C:\temp` 这类合法串会保留

典型 case：

```text
hello\nworld -> helloworld
\times -> \times
```

说明：
- 这个规则是之前一个高频误伤点，V5 已修正

---

### 14. `math_symbol_flood`

功能：
- 删除同一数学 / 符号字符的连续洪泛
- V5 清洗侧阈值：连续 `4` 次及以上删除整段

典型 case：

```text
===== -> 
──── -> 
！！！！ -> ！！！！
```

说明：
- 目标是去掉刷屏式装饰，不影响正常正文

---

### 15. `line_boundary_space`

功能：
- 删除行尾的空格 / tab
- 主要处理 `space-only blank line`

典型 case：

```text
第一段
 
第二段 -> 第一段

第二段
```

说明：
- 它不会改动普通正文中间的空格
- 只是把“空格撑起来的空行”变成真正空行

---

### 16. `blank_line_collapse`

功能：
- 把 3 个及以上连续换行压成 2 个

典型 case：

```text
A\n\n\nB -> A\n\nB
```

说明：
- 保留段落边界，但去掉过长空白

---

## 二、V5 的关键安全点

### 1. 不再按类别粗删组合符

V3 时代最容易出问题的是组合符。

V5 的策略是：

- 只删白名单里的装饰组合符
- 不按 `Mn/Me` 类别一刀切
- 多语种正文默认保留

### 2. 不再全 PUA 删除

V5 只做：

- 已知噪声精确删
- 高置信上下文修复

### 3. 语义符号保留

V5 保留：

- 复选框 / 单选框 / 打勾 / 打叉 / 实心空心圆

这对问卷、财报、法律文书尤其重要。

### 4. LaTeX 更稳

V5 的 `forbidden_strings` 已修复一个高频 bug：

- 不会把 `\times`、`\theta`、`\right` 这类合法命令的前缀误删

---

## 三、QC 算子

V5 不只是清洗，还把“清洗前后检查”单独拆出来了。

---

### 1. `mojibake_detect`

作用：
- 只检测，不清洗
- 用来发现乱码、编码残留、不可见垃圾、装饰符号、HTML 实体等

核心子规则：

| 规则 | 功能 | 典型 case |
|---|---|---|
| `fffd` | 检测替换符 `�` | `你好�世界` |
| `seq` | 检测经典中文乱码序列 | `æ–‡å­—` |
| `cscn` | 检测代理对 / 未分配码位 | 非法 Unicode |
| `c1_control` | 检测 C1 控制字符 | 0x80-0x9F |
| `double_enc` | 检测双重编码特征 | `Ã©`、`Ã¢â‚¬` |
| `cp1252` | 检测 CP1252 误解码特征 | `rÃ©sumÃ©` |
| `pua` | 检测 PUA 洪泛 | 大量私用区字符 |
| `pua_known_noise` | 检测已知 PUA 噪声 | `U+F8FF`、`U+F04A` |
| `html_entity` | 检测 HTML 实体残留 | `&gt;` |
| `bidi_control` | 检测双向控制符 | `‭John‬` |
| `decorative_symbol` | 检测装饰符号 | `┻━┻` |
| `decorative_combining` | 检测装饰组合符 | `s̶t̶r̶i̶k̶e̶` |
| `zw_flood` | 检测零宽字符混入 | `a​b` |
| `invis_garbage` | 检测 BOM / WJ / SHY 等 | `﻿` |
| `specials` | 检测 Specials / interlinear 标记 | U+FFF0-U+FFFC |
| `conditional_zw` | 检测 ZWJ/ZWNJ/LRM/RLM | 阿语 / 印地语边界异常 |
| `variation_selector` | 检测 variation selector | 字形选择符 |
| `styled_math_alnum` | 检测数学样式字母数字 | `𝓥` |
| `abnormal_space` | 检测异常空格 | NBSP / 全角空格 |
| `math_symbol_flood` | 检测符号洪泛 | `=====` |
| `control_format_flood` | 检测格式控制字符洪泛 | 多个 Cf/Zl/Zp |

典型 case：

```text
hello<br>&gt;<|im_start|>world
```

这类输入会同时命中 HTML 残留、HTML 实体、训练 token。

---

### 2. `text_sub_dedup`

作用：
- 检测连续重复片段
- 不直接清洗，由上层决定是否删除

核心入口：

```python
detect_split_content(data, language)
```

核心行为：

- 中文路径：按字符级重复检测
- 英文路径：按词级重复检测
- 连续重复次数低于阈值的不报
- 超长文本会切片，避免正则开销过大

典型 case：

```text
哈哈哈哈哈哈哈哈
This is is is is a test test test test
```

说明：
- 适合处理“段落重复”“局部模板重复”“刷屏式重复”

---

### 3. `repetition_check`

作用：
- 检测模型输出里的重复、洪泛、跑飞枚举
- 主要用于对话/长答案的后置质量检查

核心函数：

- `check_repetition_ngram(record)`
- `check_token_flood(record)`
- `check_runaway_enum(record)`

典型 case：

```text
1.
2.
3.
4.
...
```

或者：

```text
aaaaaa aaaaaa aaaaaa aaaaaa
```

说明：
- 这是对“生成结果异常”的防线，不是字符清洗

---

## 四、推荐的使用方式

### 清洗

- 直接跑 V5 默认规则
- 默认就是当前最稳的一套字符级清洗

### 检测

- 清洗前后都可以跑 `mojibake_detect`
- 长答案 / 长文拼接场景建议再跑 `text_sub_dedup` 和 `repetition_check`

### 周报表达

可以直接概括成：

> V5 相比 V3，从“少数字符清理”升级到了“字符规范化 + 语义保留 + 结构化噪声治理 + 清洗后质量检测”的完整链路。

---

## 五、retrieval_sample_500 真实样本统计

这部分直接来自 `retrieval_sample_500.jsonl` 内部已有的 `meta.cleaning_v5`，不是重新跑出来的结果。

样本概况：

- 总样本数：`500`
- 含 `meta.cleaning_v5` 的样本数：`500`
- 有清洗命中的样本数：`500`
- 版本：`v5.0`
- 统计 schema：`compact_by_rule_v1`

### 1. 总体命中画像

| 规则 | 总命中次数 |
|---|---:|
| `decorative_symbol` | 267,731 |
| `abnormal_space` | 55,079 |
| `line_boundary_space` | 53,585 |
| `math_symbol_flood` | 26,060 |
| `blank_line_collapse` | 19,751 |
| `forbidden_strings` | 7,318 |
| `control_invisible` | 4,744 |
| `styled_math_alnum` | 1,426 |
| `html_entity` | 346 |
| `emoji_all` | 151 |
| `variation_selector` | 71 |
| `bidi_control` | 21 |
| `decorative_combining` | 4 |

可以直接看出，这 500 条样本里最主要的问题不是“奇怪单字符”，而是三大类：

- 装饰性符号洪泛：`decorative_symbol`
- 异常空格 / 行边界空格：`abnormal_space` + `line_boundary_space`
- 版式级噪声压缩：`math_symbol_flood` + `blank_line_collapse`

这也说明 V5 相比早期版本，重点已经不只是“删几个怪字符”，而是在处理真实长文数据里最常见的版式噪声。

### 2. 每个算子的真实命中 case

下面这些例子都直接来自样本里的 `meta.cleaning_v5.by_rule.*.spans[0]`。

| 规则 | 样本内真实 case | 动作 | 说明 |
|---|---|---|---|
| `abnormal_space` | `\u202F` | 删除 | 窄不换行空格，样本里大量出现 |
| `abnormal_space` | `\u2003 -> 空格` | 替换 | EM SPACE 统一成普通空格 |
| `blank_line_collapse` | `\n\n` | 压缩删除 | 合并多余空行 |
| `control_invisible` | `\u200B` | 删除 | 零宽空格，不可见但会污染文本 |
| `decorative_symbol` | `────────────────────────────────────────` | 删除 | box drawing 直线装饰，命中非常高 |
| `emoji_all` | `\u200D` | 删除 | emoji ZWJ 附件字符 |
| `html_entity` | `&amp;` | 删除 | HTML 实体串整体删掉 |
| `line_boundary_space` | `两个普通空格` | 删除 | 行首/行尾多余空格 |
| `math_symbol_flood` | `==================================================` | 删除 | 连续数学/分隔符洪泛 |
| `forbidden_strings` | `\\n\\t` | 删除 | 字面量转义串，不是实际换行/制表 |
| `variation_selector` | `U+FE0F (️)` | 删除 | 只删 selector，本体保留 |
| `styled_math_alnum` | `𝔽 -> F` | 替换 | 数学样式字母规范化 |
| `decorative_combining` | `̶` | 删除 | 删除线组合符 |
| `bidi_control` | `U+202A` | 删除 | 双向文本控制符 |

### 3. 这些统计说明了什么

从这批真实样本看，V5 的收益主要落在下面几件事上：

1. `decorative_symbol` 和 `math_symbol_flood` 能明显清掉论坛/网页/转存文本里的分隔线、刷屏符号、块状装饰。
2. `abnormal_space`、`line_boundary_space`、`blank_line_collapse` 负责把版式噪声压平，这类问题在长文拼接和 OCR/PDF 来源里非常常见。
3. `forbidden_strings` 命中很多，说明字面量 `\\n`、`\\t` 污染是真问题；同时 V5 已经补了 LaTeX 边界，避免误伤 `\\times`、`\\theta` 这类公式命令。
4. `styled_math_alnum`、`bidi_control`、`decorative_combining` 这些命中量不算最大，但属于“少量高破坏性噪声”，很值得保留。

### 4. 周报里可以怎么写

可以直接写成下面这个版本：

> 基于 `retrieval_sample_500.jsonl` 的真实样本统计，V5 已从早期“少量特殊字符删除”升级为“字符规范化 + 版式噪声治理 + 结构保护”的完整清洗链路。在 500 条样本中，500 条均有清洗命中，最高频问题依次为装饰符号洪泛、异常空格、行边界空格、连续分隔符洪泛和多余空行。说明 V5 已经能覆盖真实长文数据中的主流脏数据类型，而不只是处理零散 Unicode 异常字符。
