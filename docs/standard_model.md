# 银行标准流水模型 v1

统一模型使用 pandas.DataFrame，字段名稳定，适配器必须输出 statement_schema.STANDARD_COLUMNS。一个 DataFrame 可以包含多家银行、多账号的交易，但目前币种必须为 CNY。

| 标准字段 | 类型（清洗后） | 说明 |
| --- | --- | --- |
| 银行编码 | string | 如 ABC/ICBC/CCB/GENERIC/自定义编码 |
| 银行名称 | string | 银行中文名称 |
| 账号 | string | 本方账号；Excel 无账号时使用带文件和 Sheet 标识的占位值，不跨文件合并 |
| 账户户名 | string | 本方户名 |
| 开户行 | string | 本方开户行 |
| 币种 | string | CNY，拒绝非人民币数据 |
| 交易日期 | datetime64[ns] | 交易发生日期或交易时间戳 |
| 交易时间 | string | 日内交易时间/时间戳，排序参考字段 |
| 交易流水号 | string | 原始流水号；不假设流水号具有排序语义 |
| 贷方发生额 | number | 入账金额，人民币元，非负 |
| 借方发生额 | number | 出账金额，人民币元，非负 |
| 余额 | number | 交易后账户余额，人民币元 |
| 对方账号 | string | 交易对方账户 |
| 对方户名 | string | 交易对方名称 |
| 对方开户行 | string | 对方开户行 |
| 交易用途 | string | 交易摘要，供分类及风险规则使用 |
| source_file | string | 来源文件名 |
| source_sheet | string | Excel 工作表 |
| source_row | int | 来源 Excel 实际行号（从 1 开始） |

注意：适配层严格区分借贷金额方向，任何无法判断方向的记录都停止解析。金额校验拒绝负的拆分收入/支出金额和同笔借贷双正值。数值计算使用 pandas 浮点数，银行生产级金额精度仍需引入整数分/Decimal 复核。日志和报告中的源行号需配合文件与工作表三元组使用。

## 适配策略

1. 统一读取 Excel：在前 50 行定位真实表头；跳过没有目标表头的 Sheet。
2. 识别银行：根据文件名与前导说明定位内置适配器；未识别则使用 GENERIC；可用 --bank 明确指定。
3. 字段映射：内置 aliases + 银行自定义 JSON aliases（支持额外 header）。
4. 转换收支：split=贷方/借方双列；direction=金额+收支标志；signed=带正负号金额（正数入账、负数出账，必须由模板显式配置）。
5. 强制同构：填充可选字段、来源标识，验证 STANDARD_COLUMNS，再交给 DataProcessor。

分析层严禁再读取银行原始列名。新增银行只需增加 BankAdapter 或 JSON 模板；遇到实际银行特殊业务格式（附表、多行记录、冲正等）应实现 BankAdapter 子类。
