# Quality Checks 数据清洗项目交接文档

**交接目标版本：V7**  
**当前可运行基线：Quality Checks V6.0**  
**文档日期：2026-08-30**  
**使用方式：以下路径均相对于解压后的交接包根目录，数据路径请替换为接收方自己的路径。**

这份文档是给下一位维护者和 Codex 使用的工作交接说明。它记录项目背景、已经做过的决策、当前代码真实状态、可复现命令、已知风险以及建议的 V7 开发顺序。

先看结论：当前工作区没有独立的 quality_checks_V7 目录。现在可以直接运行和交付的是 quality_checks_V6；文件名 continuous_dedup_mapper_v7.py 中的 v7 是连续去重模块的历史命名，不代表整个项目已经完成 V7。后续接手时，应以 V6 为基线建立 V7，而不是默认 V7 已经存在。

---

## 1. 给 Codex 的启动指令

接手人可以把下面这段和本文件一起发给 Codex：

~~~
你正在接手一个面向大模型训练数据的文本质量清洗项目。

请先完整阅读 QUALITY_CHECKS_V7_HANDOFF.md，再检查工作区实际文件，不要根据版本名猜测代码状态。

当前基线是 quality_checks_V6，版本元信息是 v6.0，清洗元信息键是 meta.cleaning_v6。当前没有独立的 quality_checks_V7 目录。continuous_dedup_mapper_v7.py 只是连续去重模块的文件名。

项目目标：在不删除整条记录的前提下，清理高置信度的字符级/片段级噪声，并保留可追溯的清洗元信息；更复杂的乱码、重复和连续去重问题先由 QC 报告，不要未经验证直接删除。

工作顺序：
1. 先确认输入字段、数据格式和输出路径，永远不要覆盖原始文件。
2. 先运行现有测试和一个小样本 dry-run，再改代码。
3. 修改前后都保留 summary、issues 和代表性 before/after 样例。
4. 不要按 Unicode Mn/Me 类别一刀切，不要删除整个 PUA 范围，不要删除整条数据。
5. 每新增一个清洗算子，都要加入独立回归测试、README、元信息验证和至少一个误删保护样例。
6. 如果要建立 V7，先复制/冻结 V6 基线并记录版本差异，不要直接在 V6 上无记录覆盖。

本轮优先任务：确认 V6 在 800 条 sample_100_per_domain 上的完整表现；分析 repeated_noise_token、hex_blob、dedup 和 mojibake 的残留；再决定 V7 的规则和阈值。
~~~

如果接手人只发本文件而没有代码，Codex 无法凭空访问本机路径。应同时提供本目录中的 V6 代码包，或者把整个工作区作为项目目录打开。

---

## 2. 项目目标和基本原则

### 2.1 业务目标

这套工具用于清洗和质检给大模型训练使用的长文本、文章、评论和多轮对话数据。主要问题包括：

- 编码错误、mojibake 和替换符
- emoji、装饰符号、variation selector 和不可见字符
- PUA 私用区字符造成的字体映射噪声
- HTML 残留、训练特殊 token、错误字面量转义
- 过多空行、行尾空格、异常空格
- 长十六进制/二进制转储文本
- 模型生成或网页抽取产生的连续重复片段
- 文章评论中的截断标题和重复引用

### 2.2 已确定的原则

1. **不删除整条记录。** 只删除或替换字段中的命中字符/片段。
2. **清洗和质检分离。** 高置信度的字符噪声可以清洗；语义复杂的问题先报告。
3. **默认保护语义。** 不按 Mn/Me Unicode 类别粗删，不全量删除 PUA，不把所有符号都当 emoji。
4. **保留溯源。** JSONL 清洗可写入 meta.cleaning_v6，记录规则、原文片段、起止位置和替换动作。
5. **先小样本验证，再全量运行。** 任何阈值变化都要保留旧输出与新输出做对照。
6. **原始数据不可覆盖。** 输入、阶段产物、最终产物、QC 报告分开保存。

---

## 3. 当前架构

推荐的数据处理顺序：

~~~
原始 JSONL/XLSX
    │
    ├─ 可选：clean_article_comment.py
    │       文章截断标题清理、评论引用标记
    │
    ├─ quality_checks_V6/clean_variation_chars.py
    │       字符级和片段级高置信度清洗
    │       输出 cleaned JSONL/XLSX + meta.cleaning_v6
    │
    ├─ run_long_dialog_quality_checks.py
    │       mojibake / repetition / dedup 质检
    │       输出 issues.jsonl、failed_records.jsonl、summary.json
    │
    └─ 人工抽样、规则调参、训练数据交付
~~~

### 3.1 清洗链路

入口文件：

- quality_checks_V6/clean_variation_chars.py
- 单条文本入口：clean_text(text, rules=DEFAULT_RULES)
- JSONL 入口：clean_jsonl(...)
- XLSX 入口：clean_xlsx(...)

清洗器只处理命令行 --fields 指定的字段。默认字段是 content。JSONL 里没有字符串值的字段会跳过，记录本身不会被删除。

### 3.2 QC 链路

入口文件：

- quality_checks_V6/run_long_dialog_quality_checks.py
- 单条接口封装：quality_checks_V6.quality_api.check_record

当前 QC 检查三类问题：

| 检查 | 作用 | 是否修改正文 |
|---|---|---|
| mojibake | 编码错配、替换符、异常 Unicode、HTML/PUA 等 | 否 |
| repetition | n-gram 重复、token flood、连续枚举跑飞 | 否 |
| dedup | 连续子串/词/字符重复，默认连续 5 次以上 | 否 |

QC 输出问题和 highlights，不会自动删除记录或正文。

### 3.3 API 链路

入口文件：

- quality_checks_V6/quality_api.py

Python 导入：

~~~
from quality_checks_V6 import clean_record, check_record, clean_and_check
~~~

HTTP 端点：

- GET /health
- GET /v1/rules
- POST /v1/clean
- POST /v1/check
- POST /v1/clean-and-check

API 是单条记录接口。大文件应使用 CLI，避免 HTTP 序列化和请求体开销。

---

## 4. 版本演进

工作区中的版本是并列目录和压缩包，不是 Git 分支。当前工作区也不是 Git 仓库，不能依赖 git log 恢复历史。

| 版本 | 主要内容 | 当前定位 |
|---|---|---|
| 早期 quality_checks | 主要是 QC 检测模块 | 历史基线 |
| quality_checks_V2 | 基础 variation selector、styled math alnum 清洗 | 历史版本 |
| quality_checks_V3 | 增加 emoji、组合符等字符清洗 | 历史版本 |
| quality_checks_V3_5 | PUA 上下文修复、控制字符、换行、元信息等 | 历史生产实验版本 |
| quality_checks_V4 | 尝试更细的 emoji/复杂脚本保护和规则配置 | 历史实验版本 |
| quality_checks_V5 | 完整清洗链路、QC 前后检查、API、compact metadata、hex 规则 | 历史基线/兼容版本 |
| quality_checks_V6 | 在 V5 上加入 repeated_noise_token，元信息升级为 cleaning_v6 | 当前可运行基线 |
| V7 | 尚未建立独立目录 | 下一阶段工作 |

### 4.1 V5 到 V6 的实际变化

V6 不是完全重写，主要变化在 clean_variation_chars.py：

- CLEANING_META_VERSION: v5.0 -> v6.0
- cleaning_v5 -> cleaning_v6
- _cleaning_v5 -> _cleaning_v6
- 默认规则加入 repeated_noise_token
- API 服务标识改为 quality_checks_v6
- 增加 quality_checks_V6/tests/test_repeated_noise_token.py
- 文档和 API 标识统一为 V6

hex_blob 在 V5 阶段已经加入，V6 继承它，不是 V6 首次新增。

### 4.2 V7 的版本边界

目前不能对外宣称已经有完整 V7。建立 V7 时至少要完成：

1. 冻结一份 V6 基线包和 checksum。
2. 复制为 quality_checks_V7，统一版本号、服务名、元信息键。
3. 把 V7 新规则写入默认规则表、README、API /v1/rules 和测试。
4. 在 800 条样本上跑 before/after/QC 对照。
5. 记录规则命中量、剩余问题量和误删样例。

---

## 5. V6 默认清洗规则

源码中的权威顺序是 quality_checks_V6/clean_variation_chars.py 的 DEFAULT_RULES。当前顺序如下：

~~~
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

### 5.1 字符和格式噪声

| 算子 | 当前动作 | 重要边界 |
|---|---|---|
| variation_selector | 删除 U+FE00-U+FE0F 和补充区 variation selector | 默认只删 selector，不删前面的普通 base |
| styled_math_alnum | NFKC 转成普通字母/数字 | 例如 𝙤 -> o，保留语义而不是删除 |
| abnormal_space | NBSP、EM SPACE、全角空格转普通空格；窄 NBSP/thin space 删除 | 不全局压缩正常空格 |
| bidi_control | 删除 bidi embedding/override/isolate 及 LRM/RLM | 不修改可见正文 |
| control_invisible | 删除 BOM、零宽字符、word joiner、soft hyphen 等 | U+2028/U+2029 由 line_separator 处理 |
| line_separator | U+2028 -> LF，U+2029 -> LF LF | 后续可能触发空行压缩 |
| line_boundary_space | 删除行尾和文本尾的空格/Tab | 不全局删除 Tab |
| blank_line_collapse | 连续换行最多保留两个 | 保留段落边界 |

### 5.2 emoji、装饰符号和组合符

| 算子 | 当前动作 | 风险 |
|---|---|---|
| emoji_all | 删除 emoji 本体、ZWJ、keycap/enclosing 附件和常见 emoji variation sequence | 范围较激进；当前场景已决定 emoji 默认视为训练噪声 |
| decorative_symbol | 删除 box drawing、block、geometric、misc symbols、dingbats | 保留部分表单/语义符号，如 □、☐、☑、✓、○、● |
| decorative_combining | 只删除明确白名单 U+0336、U+033F、U+035C-U+0361 | 不按 Mn/Me 类别一刀切 |

阿拉伯文、希伯来文、印地文等语言需要的组合符默认保留。不要为了“洗干净”而启用 legacy 组合符规则。

### 5.3 PUA

PUA 不是全部删除。当前策略是：

- pua_known_noise 删除已知噪声 U+F8FF、U+F04A
- pua_contextual 在高置信上下文中修复或删除
- 未知 PUA 保留，交给 QC 报告

已知上下文修复包括：列表 bullet、数字范围符、电子书点号、英文名连字符/撇号、脚注 marker，以及少量高置信缺字映射。遇到新 PUA 时先抽样确认语义，不要直接扩大全范围删除。

### 5.4 HTML、训练 token 和符号洪泛

| 算子 | 当前阈值/动作 |
|---|---|
| html_entity | 删除 named、decimal、hex HTML entity，例如 &gt;、&#123;、&#x1F; |
| forbidden_strings | 删除 <br>、模型特殊 token、工具调用标签、错误字面量 \n/\t/\r 等 |
| math_symbol_flood | 同一个数学/符号字符连续 4 次及以上，删除整段 |

forbidden_strings 对 LaTeX 做了保护：\times、\theta、\right、\r{a} 等不应被当成错误字面量删除。

### 5.5 hex_blob

当前阈值是连续 64 个以上十六进制字符，换行包裹的高 hex 比例块也可能被清理。0x 前缀的代码常量受边界保护，但合法的 64 位 hash 可能被识别为 hex blob。

因此：

- 64 位以上连续 hex：默认删除
- 63 位及以下：保留
- 32/40/64 位 hash：当前 V6 不再自动豁免，合法 hash 数据需要使用自定义规则或后续增加上下文保护
- 正常英文、普通代码和 0x 常量：已有回归测试保护

这是 V7 需要优先重新评估的规则之一。

### 5.6 repeated_noise_token

这是 V6 新增的高置信度重复噪声清洗规则，针对类似：

~~~
a111111111 a111111111 a111111111 a111111111 a111111111
~~~

当前条件：

- 同一 token 连续至少 5 次
- token 同时含字母和数字
- token 内部有至少 4 个连续相同字符
- token 之间可用空格、换行、逗号、分号等分隔
- Markdown triple-backtick 代码块内不处理

4 次重复、普通单词、纯数字、普通 ID、0x 常量和代码块应保留。当前元信息的 rule_counts 按删除字符数统计，不是按 token 个数统计。例如 5 个 a111111111 加 5 个空格会显示 count 55。

---

## 6. QC 检测规则和如何理解结果

### 6.1 mojibake

检测编码错配和异常字符，包括：

- U+FFFD 替换符
- GBK/UTF-8/Latin-1 错解码序列
- Windows-1252 错解码
- C1 控制字符
- PUA 异常、HTML entity、bidi、装饰符号、不可见垃圾
- variation selector、styled math alnum 等

它返回命中区间和字符级信息，不改正文。numpy 是 mojibake QC 的依赖；只调用 /v1/clean 时 API 延迟初始化，不一定需要 numpy，但调用 /v1/check 或 clean-and-check 需要完整依赖。

### 6.2 repetition 和 n-gram

repetition 主要检测：

- 5-gram/10-gram 的高比例重复
- 单 token flood
- runaway enumeration

它面向模型输出循环，不等于所有重复文本都应该删除。

### 6.3 dedup / sub_dedup_continuous

核心文件是 text_sub_dedup/continuous_dedup_mapper_v7.py。当前常量 MIN_CONTINUOUS_REPEAT_NUM = 5，英文路径主要按词，中文/非英文路径主要按字符或片段。输出包括：

- offset：建议处理的重复区间
- score：重复次数
- repeat_text_offset：第一次出现的重复单元
- char_len / word_len
- model_type=sub_dedup_continuous

这就是为什么：

~~~
a111111111 a111111111 a111111111 a111111111 a111111111
~~~

在旧版本 QC 中显示为 sub_dedup_continuous，而不是 repetition_ngram。它属于连续重复片段检测，广义上可以称为重复 n-gram，但实现归类不同。V6 已把这种特定高置信度形状转成 repeated_noise_token 清洗规则；一般 dedup 仍然只报告。

### 6.4 已知误报

历史样本中出现过如下形式：

~~~
右 左 右左 右 左右 左
~~~

它可能是表格、演示或棋谱样式，不一定是模型刷屏。短重复单元不要直接清洗；V7 应优先在 QC 侧加入表格/结构识别或提高短片段阈值。

---

## 7. 清洗元信息

V6 推荐 --add-cleaning-meta --meta-detail spans。每条 JSONL 记录会得到：

~~~json
{
  "meta": {
    "cleaning_v6": {
      "version": "v6.0",
      "schema": "compact_by_rule_v1",
      "line_no": 1,
      "changed": true,
      "fields": ["content"],
      "changed_fields": ["content"],
      "change_count": 55,
      "rule_counts": {
        "repeated_noise_token": 55
      },
      "by_rule": {
        "repeated_noise_token": {
          "count": 55,
          "field_counts": {"content": 55},
          "span_count": 1,
          "spans": [
            {
              "field": "content",
              "start": 100,
              "end": 155,
              "count": 55,
              "action": "delete",
              "original_preview": "a111111111 ...",
              "replacement_preview": "",
              "reasons": ["repeat_count=5"]
            }
          ]
        }
      }
    }
  }
}
~~~

注意：

- offset 是原始字段的 Unicode 字符偏移，start 包含，end 不包含
- rule_counts 和 change_count 是字符/替换单元数量，不是问题样本数
- meta_detail=chars 或 both 会显著增大文件，生产默认使用 spans
- 原记录已有 dict 类型 meta 时写入 meta.cleaning_v6
- 原 meta 不是 dict 时写入 _cleaning_v6，避免覆盖源字段

---

## 8. 文章评论前置脚本

文件：clean_article_comment.py

它不属于 V6 API 包，也没有融合进 V6。推荐作为独立前置阶段：

~~~
原始 article/comment JSONL
  -> clean_article_comment.py
  -> quality_checks_V6
~~~

脚本有两个功能：

1. strip_truncated_titles(content)
   - 查找 <|start_of_articleid=N|> 后面以 Unicode 省略号 … 结束的标题行
   - 删除标题文本，保留 article marker
   - 只识别 …，不识别三个 ASCII 点 ...
2. tag_comment_quotes(content)
   - 在 comment start/end marker 之间分割评论
   - 如果当前段落与前面评论相似度达到 0.85，包成 <ref id=N>...</ref>
   - 最少 40 个字符、至少 8 个单词
   - 排除纯 URL 和部分编辑样板

它会修改结构和新增标签，不能直接替代字符清洗。脚本默认是 demo 模式，只有加 --run 才写盘。运行前先确认 --src、--dst 和 --pattern，不能让输出目录覆盖源目录。

已知风险：相似度规则可能把改写段落当成引用；评论必须依赖空行 \n\n 分隔。正式全量运行前应先抽样查看 <ref> 标记。

---

## 9. 其他脚本的定位

### content_checker.py

这是面向 Aegis/EB5 数据结构的生产 QC 插件，不是独立的 JSONL 清洗器。它依赖外部包：

- aegis_data.task.data
- aegis_data.task.plugin_base
- codingplan_ret_data_transformer
- verify_stats_decorator

它主要检查 assistant 的占位符、低质内容、think 标签、工具调用污染和 API key 形态。没有生产依赖时不能直接在本工作区运行。

### error_strings_checker.py

同样是生产 QC 插件，检查 <br>、&lt;br&gt;、控制字符和字面量 \n 等特殊字符串。它只报告，不清洗。

这两个插件与 V6 的关系是：规则思想有重叠，但数据结构和运行框架不同。不要为了“统一”而直接复制粘贴或融合，先确认生产插件接口和返回协议。

---

## 10. 可复现操作

### 10.1 安装依赖

在解压后的交接包根目录执行：

~~~
python3 -m pip install -r quality_checks_V6/requirements.txt
~~~

依赖：

- numpy>=1.20：mojibake QC
- openpyxl>=3.0：XLSX 清洗
- JSONL 字符清洗本身只用 Python 标准库

### 10.2 先跑小样本

~~~
python3 quality_checks_V6/clean_variation_chars.py \
  --input sample_100_per_domain.jsonl \
  --output /tmp/sample_100.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 2 \
  --max-in-flight 4 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /tmp/sample_100.v6.clean_summary.json
~~~

output 必须和 input 不同。正式运行时把 /tmp 换成明确的项目输出目录。

### 10.3 JSONL 清洗和清洗后 QC

~~~
python3 quality_checks_V6/clean_variation_chars.py \
  --input /path/to/input.jsonl \
  --output /path/to/input.v6.cleaned.jsonl \
  --format jsonl \
  --fields content \
  --workers 16 \
  --max-in-flight 32 \
  --add-cleaning-meta \
  --meta-detail spans \
  --summary /path/to/input.v6.clean_summary.json \
  --qc after \
  --qc-output-dir /path/to/input.v6.qc
~~~

QC 也可以单独跑：

~~~
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input /path/to/input.v6.cleaned.jsonl \
  --output-dir /path/to/input.v6.qc \
  --checks all \
  --workers 16 \
  --executor process \
  --no-passed-output \
  --failed-records-output /path/to/input.v6.qc/failed_records.jsonl
~~~

建议保存：

- *.cleaned.jsonl
- clean_summary.json
- qc/summary.json
- qc/issues.jsonl
- qc/failed_records.jsonl

在受限的 macOS/Codex 运行环境中，如果 `--executor process` 因
`os.sysconf("SC_SEM_NSEMS_MAX")` 权限错误而失败，可改用线程执行器完成同一
份 QC：

~~~
python3 quality_checks_V6/run_long_dialog_quality_checks.py \
  --input /path/to/input.v6.cleaned.jsonl \
  --output-dir /path/to/input.v6.qc \
  --checks all \
  --workers 2 \
  --executor thread \
  --no-passed-output \
  --failed-records-output /path/to/input.v6.qc/failed_records.jsonl
~~~

这属于运行环境限制，不改变检测规则；在普通服务器上仍可按内存和进程
开销选择 `process` 或 `thread`。

### 10.4 XLSX 清洗

~~~
python3 quality_checks_V6/clean_variation_chars.py \
  --input input.xlsx \
  --output output.v6.cleaned.xlsx \
  --format xlsx \
  --fields variation_context \
  --sheet Sheet1 \
  --summary output.v6.clean_summary.json
~~~

XLSX 的 --fields 是表头名称，不是列号。

### 10.5 前置处理文章评论

先 demo：

~~~
python3 clean_article_comment.py \
  --src /path/to/article_comment_dataset \
  --pattern 'ars_*.jsonl' \
  --demo 5
~~~

确认样例后再写盘：

~~~
python3 clean_article_comment.py \
  --src /path/to/article_comment_dataset \
  --dst /path/to/article_comment_dataset_cleaned \
  --pattern 'ars_*.jsonl' \
  --workers 32 \
  --run
~~~

再把前置产物交给 V6 清洗。

### 10.6 启动 HTTP API

~~~
python3 quality_checks_V6/quality_api.py \
  --host 127.0.0.1 \
  --port 8000
~~~

服务默认没有认证和 TLS。只在可信内网使用 0.0.0.0，生产环境应放在已有认证网关之后。

---

## 11. 原工作区的重要数据和历史产物

以下路径用于记录历史实验背景。原始数据、清洗结果和旧 ZIP 均未随本交接包提供；
接收方不能假定这些文件存在，需要使用自己的数据重新建立基线。

| 路径 | 作用 |
|---|---|
| sample_100_per_domain.jsonl | 800 条样本，主要回归数据 |
| mrcr_merged_sample_1000.jsonl | 1000 条 MRCR 样本，较长，历史 QC 压测数据 |
| retrieval_sample_500.jsonl | 500 条 retrieval 样本 |
| final_datalist_merged.xlsx | 早期质检 Excel 数据 |
| quality_checks_V6/ | 当前代码基线 |
| quality_checks_V6_api_20260811.zip | 已验证的 V6 API 包 |
| V5_function_principles.md | 函数级原理说明，内容以 V5 为主，接手时需按 V6 修订 |
| V5_operator_catalog.md | 算子目录和历史 case，内容以 V5 为主 |
| quality_checks_weekly_report.md | 历史进展和样本复查记录 |
| sample_100_changed_review.md | 100 条样本的残留问题复查 |
| outputs/ | 历史 Excel 质量报告和清洗摘要 |
| qc_* | 历史 QC 输出目录 |

### 11.1 数据规模记录

按当前工作区文件计算的 UTF-8 JSONL 文件大小：

| 文件 | 记录数 | 文件字节数 |
|---|---:|---:|
| sample_100_per_domain.jsonl | 800 | 64,559,603 |
| mrcr_merged_sample_1000.jsonl | 1,000 | 781,570,085 |
| retrieval_sample_500.jsonl | 500 | 374,198,030 |

这些是完整 JSONL 行的 UTF-8 字节数，不是 content 字符数，也不是 token 数。

### 11.2 历史运行结果

以下结果来自历史 V3/V3.5 运行，用于理解数据难度，不代表 V6/V7 的最终结果：

| 数据/版本 | 总数 | 通过 | 失败 | 主要问题 | 用时 |
|---|---:|---:|---:|---|---:|
| sample_100_per_domain 原始 QC | 800 | 401 | 399 | mojibake 333，dedup 103 | 73.702 秒 |
| V3.5 清洗后 QC | 800 | 783 | 17 | mojibake 1，dedup 16 | 67.828 秒 |
| current rules 清洗后 QC | 800 | 776 | 24 | mojibake 8，dedup 16 | 65.867 秒 |
| V6.0 清洗前 QC（2026-08-25） | 800 | 422 | 378 | mojibake 367，dedup 16 | 348.280 秒* |
| V6.0 清洗后 QC（2026-08-25） | 800 | 783 | 17 | mojibake 1，dedup 16 | 345.423 秒* |
| mrcr_merged_sample_1000 原始 QC | 1,000 | 27 | 973 | mojibake 958，dedup 647，repetition 9 | 540.919 秒 |

\* 本次在 Codex 受限环境中使用 `--executor thread --workers 2`，速度约
2.30 records/s；这是 QC 耗时，不包含清洗写盘时间。

本次 V6 清洗配置为 `fields=content`、`--add-cleaning-meta`、
`--meta-detail spans`。800 条记录全部保留，537 条记录的 `content` 发生
局部变化，累计变化统计为 45,526 个字符/替换单元，800 条记录均带有
`meta.cleaning_v6`。主要命中统计为：`control_invisible` 13,554、
`line_boundary_space` 9,618、`blank_line_collapse` 8,033、
`abnormal_space` 5,546、`decorative_symbol` 4,261、`emoji_all` 1,880、
`variation_selector` 1,011、`hex_blob` 480。

正式基线产物曾位于原工作区的 `handoff_v6_validation_20260825/`；交接压缩包只带摘要，
不带原始 JSONL、清洗后的大 JSONL 或包含上下文的 issues 明细。

---

## 12. 当前已验证内容

最近一次 V6 代码验证已经通过：

- V6 Python 模块导入
- 所有 V6 Python 文件编译
- repeated_noise_token 6 组单元测试
- 5 次重复删除、4 次重复保留
- 换行和逗号分隔重复 token
- 普通单词、年份、纯数字、普通 ID 保留
- Markdown fenced code 保留
- hex_blob 64 位边界
- 0x 常量保护
- emoji、BOM、重复 token、hex 同时存在时按不同规则记录
- HTTP /health、/v1/rules、/v1/clean 返回 200
- 从 ZIP 路径直接导入 V6 并清洗

本次交付的文件校验值以包根目录的 `SHA256SUMS.txt` 为准。压缩包本身的校验值
在包外另附的 `.sha256` 文件中提供；如果代码或文档继续变化，必须重新生成这两份
校验值，不要沿用历史版本的 checksum。

---

## 13. 已知风险和不能直接做的事

### 13.1 版本和文档风险

- 当前没有独立 V7；不能把 V6 包改名后就称作 V7。
- V5_function_principles.md、V5_operator_catalog.md 是历史文档，部分元信息键和规则列表仍写 V5，需要按 V6/V7 修订。
- 修改规则后要同步文档和 API /v1/rules。

### 13.2 误删风险

- emoji_all 范围较宽，部分 Unicode 符号在不同数据源中可能有语义。
- hex_blob 64 位阈值可能误删合法 hash 或机器数据。
- repeated_noise_token 虽然有字母、数字和内部重复条件，重复 5 次的特殊 ID/日志 token 仍可能被删。
- 未知 PUA 不会自动清理，QC 仍可能报 PUA。
- 不要把 sub_dedup_continuous 的每一个命中都转成清洗动作，短表格、棋谱、选项、歌词都可能产生合法重复。

### 13.3 运行风险

- 大 JSONL 使用多进程时要控制 --workers 和 --max-in-flight，长记录会显著增加内存。
- MRCR 样本明显比 800 条样本慢，不能用小样本速度直接估算全量。
- QC 的 mojibake 路径需要 numpy；XLSX 需要 openpyxl。
- API 是单条请求接口，不能把整个大文件一次塞进 HTTP。
- 没有认证和 TLS，不要把服务直接暴露到公网。

### 13.4 数据安全风险

- 原始 JSONL/XLSX 可能包含内部数据，不要把完整数据提交到公共仓库或第三方服务。
- content_checker.py 的规则会检查 API key 形态，但 V6 清洗器不是秘密扫描器；生产数据仍需独立脱敏。
- 不要修改接收方的 Codex 全局配置或环境文件，也不要通过改全局代理配置解决网络问题。

---

## 14. V7 建议开发顺序

### P0：冻结基线和跑全量

1. 复制当前 quality_checks_V6 为工作分支或新目录，保留原 V6。
2. 用 sample_100_per_domain.jsonl 跑完整 V6 清洗，开启 meta_detail=spans。
3. 跑清洗前/清洗后 QC，保存 summary、issues、failed records。
4. 统计每个清洗算子的记录数和字符数，抽样检查至少 20 条。

### P1：修正版本和测试体系

1. 建立 quality_checks_V7，统一 v7.0、cleaning_v7、服务名和 README。
2. 把 V6 已有的 6 个重复噪声测试复制为 V7 回归测试。
3. 增加真实 fixture：文章论文、阿拉伯文、印地文、LaTeX、表格、代码、PUA、emoji、hex、评论 marker。
4. 增加 CLI、Python API、HTTP API 三层 smoke test。

### P1：优化重复和误报

1. 保留 repeated_noise_token 作为窄规则，不扩大为通用 dedup 删除器。
2. 给 sub_dedup_continuous 增加表格/棋谱/列表结构保护。
3. 把重复阈值参数化，报告中同时显示重复单元数、重复次数和删除字符数。
4. 把 rule_counts 的字符数与命中 span 数分开，避免用户把 55 误读成 55 个 token。

### P1：重新评估 hex

1. 统计真实数据中 64、96、128 位 hash 的数量。
2. 增加上下文保护：hash=...、JSON key、URL、代码块、数据库 ID 等。
3. 保留高置信度长 dump 删除，但把合法 hash 默认保留或改为可配置。
4. 所有阈值必须有 before/after 误删样例。

### P2：处理文章评论结构

1. 保持 clean_article_comment.py 独立，不要直接把结构规则塞进字符清洗器。
2. 为标题截断和 <ref> 标记分别增加统计和审查输出。
3. 评估 Unicode … 与 ASCII ... 是否都要支持。
4. 把相似度阈值、最小字符数、最小词数改成可配置参数。

### P2：工程化

- 增加 pyproject.toml 或明确的可安装包入口
- 增加 CI：compile、unit test、API smoke、zip manifest
- 为每个版本生成 CHANGELOG 和 checksum
- 增加批量 API，或明确继续使用 CLI
- 为长文本性能记录 records/s、UTF-8 bytes/s、content chars/s 和内存峰值

---

## 15. 接手后的最小验收清单

完成任何 V7 变更后，至少确认：

- [ ] 原始输入文件没有被覆盖
- [ ] python3 -m py_compile 通过
- [ ] 单元测试通过
- [ ] V6/V7 规则列表和 README 一致
- [ ] API /health 版本正确
- [ ] API /v1/rules 暴露新规则
- [ ] JSONL 记录数前后一致
- [ ] meta.cleaning_v7 的 offset 是原始文本 offset
- [ ] 至少一个应清理样例和三个误删保护样例通过
- [ ] 清洗前后 QC 都跑过
- [ ] 问题样本有 issues.jsonl 和上下文
- [ ] 产物有独立目录和 checksum
- [ ] 对阿拉伯文、希伯来文、印地文、LaTeX、代码、表格做过抽样

---

## 16. 关键文件索引

相对于交接包根目录：

- quality_checks_V6/clean_variation_chars.py：V6 清洗主链路
- quality_checks_V6/run_long_dialog_quality_checks.py：QC 文件入口
- quality_checks_V6/quality_api.py：Python/HTTP API
- quality_checks_V6/mojibake_detect/：乱码检测
- quality_checks_V6/repetition_check/loop.py：n-gram、token flood、枚举检测
- quality_checks_V6/text_sub_dedup/continuous_dedup_mapper_v7.py：连续重复检测
- quality_checks_V6/tests/test_repeated_noise_token.py：V6 新增规则测试
- clean_article_comment.py：文章评论前置处理
- optional_internal_plugins/content_checker.py：外部 Aegis/EB5 生产 QC 插件
- optional_internal_plugins/error_strings_checker.py：外部特殊字符串 QC 插件
- legacy_notes/V5_function_principles.md：历史函数原理说明
- legacy_notes/V5_operator_catalog.md：历史算子目录
- legacy_notes/quality_checks_weekly_report.md：历史进展报告
- legacy_notes/sample_100_changed_review.md：历史样本复查和风险记录

---

## 17. 本次交付文件

本次交付的是 `quality_checks_handoff_v7_20260830.zip` 及其同名 `.sha256` 文件。
ZIP 包含 V6 代码、交接文档、Codex 启动提示、历史算子说明、可选内部插件说明、
人工构造的 smoke-test 样例和不含正文的 800 条基线摘要。

ZIP 排除了原始 JSONL/XLSX、大型清洗结果、QC 上下文明细、旧压缩包、
`__pycache__` 和 `*.pyc`。交接包中的版本名 `V7` 表示“面向 V7 继续开发的
交接”，不表示其中已经存在完整 V7 代码；可运行代码仍是 V6.0。

## 18. 最后给接手人的一句话

这个项目已经从“删几个奇怪字符”发展成了两条分离的链路：一条负责高置信度、可追溯的局部清洗，另一条负责发现需要人工或策略判断的质量问题。继续开发时，最重要的不是让命中数量变大，而是让每一次删除都能解释、复现和回滚。V7 应先把基线和评估补齐，再扩展规则。
