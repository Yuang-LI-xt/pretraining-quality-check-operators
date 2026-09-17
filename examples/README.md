# 合成 Smoke Test

`demo_input.jsonl` 只包含人工构造的短样例，不来自业务数据。它覆盖：

- 数学样式字母、emoji 复合序列和 `repeated_noise_token`；
- U+2028、U+2029、BOM；
- LaTeX 命令、字面量 `\\n`、HTML 标签和 HTML entity；
- 长十六进制块；
- 表单语义符号和 `▪【️】` 历史案例。

运行命令见包根目录 `README.md`。输出应写到包外的临时目录。

特别注意：demo 中的 64 字符 hex 串用于验证 `hex_blob` 的当前行为，不能据此
证明所有 64 字符串都是噪声；合法 hash/ID 的上下文保护仍是 V7 待办事项。
