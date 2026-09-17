# sample_100_changed 清洗结果复查

文件：

```text
样本文件路径：原工作区本机路径（文件未随交接包提供）
```

样本规模：

- 记录数：100
- JSON 解析失败：0
- domain 分布：
  - entertainment：26
  - medicine：24
  - industry：18
  - law：16
  - finance：8
  - humanities：5
  - technology：3

## 1. 总体结论

这批数据是服务器上用旧版 V3.5 清洗脚本跑出的结果，`meta.cleaning_v3_5.rules` 中还没有新增的 `line_boundary_space` 规则。

复查发现，主要残留问题集中在：

- 行尾空格 / tab
- 只包含空格的伪空行
- 过多连续空行
- 少量正文多空格
- 少量 tab 分隔符
- 1 条 PUA 字符未修复导致 mojibake QC 失败
- 1 条连续重复检测疑似误报

## 2. 已由新规则解决的问题

旧版清洗结果中发现：

```text
space_before_newline: 1698
space_or_tab_only_line: 156
```

典型 case：

```text
上一段\n \n下一段
特此通知。 \n上海市市场监督管理局
编:市民\t章:食品安全\t节:综合管理\t\n
```

这类问题已经由新增规则 `line_boundary_space` 覆盖。

用当前本地最新版脚本重新清洗后：

```text
space_before_newline: 0
space_or_tab_only_line: 0
```

新增规则本次会额外清理：

```json
{
  "changed_line_boundary_space": 1765
}
```

结论：服务器需要重新拉取/同步最新版 `clean_variation_chars.py` 后重跑。

## 3. 仍然残留的问题

### 3.1 过多连续空行

当前最新版重跑后仍有：

```text
many_blank_lines: 112
```

典型 case：

```text
Thanks to NetGalley for the advanced reading copy in exchange for an honest review.




stars：3；up：11；user：Kristy；comments：...
```

以及网页/Markdown 抽取文本中常见的：

```text
[Andrew Beattie]



[Full Bio]
```

建议新增规则：

```text
blank_line_collapse
```

将连续 4 个及以上换行压缩为 2 个换行：

```text
\n\n\n\n -> \n\n
```

收益：

- 减少无意义 token
- 保留段落边界
- 不破坏正常段落结构

风险较低。

### 3.2 正文多空格

当前最新版重跑后仍有：

```text
multi_space: 42
```

典型 case：

```text
简介   胆石症（gallstones）...
治疗方法   胆石病胆石症的治疗目的...
```

以及英文书评/网页文本中的：

```text
Master basic programming techniques and best practices   Harness the power...
```

建议谨慎处理。可以考虑新增规则：

```text
inline_space_collapse
```

但不要无脑全局替换所有多空格，原因是：

- 有些多空格可能来自表格/列表排版
- 有些代码或 Markdown 缩进不应被破坏

更稳的策略是：

- 只处理同一行内 3 个及以上普通空格
- 排除明显代码块或缩进行
- 或先只作为 QC warning，不直接清洗

### 3.3 Tab 字符

当前最新版重跑后仍有：

```text
tabs: 38
```

主要来源有两类：

法规元信息：

```text
编:市民\t章:食品安全\t节:综合管理
```

英文项目符号：

```text
•\tThe Garden Party.
```

建议新增可选规则：

```text
tab_normalize
```

将正文中的 tab 替换为普通空格：

```text
\t -> " "
```

注意：行尾 tab 已由 `line_boundary_space` 删除，不需要单独处理。

## 4. QC 复查结果

对 `sample_100_changed.jsonl` 跑完整 QC：

```json
{
  "total": 100,
  "passed": 98,
  "failed": 2,
  "issue_mojibake": 1,
  "issue_sub_dedup_continuous": 1
}
```

### 4.1 PUA 字符未修复

失败记录：

```text
line_no: 13
data_id: 710002228_b561fe550ecdbb99f1a288c5cb04853b_1
domain: technology
```

最初命中字符：

```text
U+E010
```

典型文本：

```text
1选数游戏
2以败取胜
3妙手传说
K迪克西特
J奈尔伯夫
```

进一步复查发现，同一条记录中还有其他电子书字体映射 PUA：

```text
U+E010：点号，修复为 `．`
U+E011：英文人名连字符，修复为 `-`
U+E10B：英文所有格撇号，修复为 `'`
U+E11A / U+E11B / U+E11C：电子书脚注标记，删除
U+E618：项目符号，修复为 `-`
U+E1BD：高置信中文缺字，`方法束` 修复为 `方法约束`
```

该问题已在本地最新版 `pua_contextual` 中修复。重新清洗 `sample_100_changed.jsonl` 后：

```text
pua_any: 0
issue_mojibake: 0
```

### 4.2 连续重复检测疑似误报

失败记录：

```text
line_no: 66
data_id: 710002158_04bfe34b833693453fce75c55878fc1f_1
domain: technology
```

命中片段：

```text
右 左 右左 右 左右 左
```

上下文：

```text
男 右 左 右 左 右 左右
女 右 左右 左 右左 右 左右 左
```

判断：这是表格/演示型文本，不是模型生成重复或真实脏数据。当前 `sub_dedup_continuous` 对短重复单元 `右 左` 比较敏感，导致误报。

建议优化 QC，而不是清洗正文：

- 对 `char_len <= 2` 的连续重复片段提高阈值
- 如果命中上下文包含明显表格/演示结构，可以降级为 warning
- 或要求短重复片段必须连续重复更长长度才判 failed

## 5. 建议优先级

### P0：已完成，服务器需重跑

- 使用最新版 `line_boundary_space`
- 解决行尾空白和伪空行问题

### P1：已完成，服务器需重跑

- `blank_line_collapse`：压缩过多连续空行
- `pua_contextual` 扩展：修复电子书 PUA 标点、脚注、项目符号和个别高置信缺字

### P2：建议观察后加

- `inline_space_collapse`：谨慎压缩正文中 3 个及以上连续空格

说明：本轮决定不做 `tab_normalize`，正文中间的 `\t` 先保留，只删除行尾 tab。

### P3：QC 调参

- 优化 `sub_dedup_continuous` 对短重复片段的误报
- 对表格/演示型文本降级处理

## 6. 当前建议

短期建议：

1. 服务器同步最新版脚本后重跑 clean。
2. 再对重跑后的结果执行 QC。
3. 剩下的 `sub_dedup_continuous` 短重复误报在 QC 侧调参，不建议清洗正文。

本地最新版重跑验证：

```text
space_before_newline: 0
space_or_tab_only_line: 0
many_blank_lines: 0
pua_any: 0
tabs: 38
multi_space: 42
```

QC 结果：

```json
{
  "total": 100,
  "passed": 99,
  "failed": 1,
  "issue_sub_dedup_continuous": 1
}
```

不建议：

- 不建议删除所有 `\n`
- 不建议全局删除所有普通空格
- 不建议直接清洗掉表格型短重复内容
