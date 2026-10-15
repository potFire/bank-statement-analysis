# 银行信贷流水审核辅助分析

基于 [weiwill88/bank-statement-analysis](https://github.com/weiwill88/bank-statement-analysis) 的职责分层思路实现多银行格式接入、统一流水清洗、现金流分析、异常识别及报告生成。本仓库使用独立实现代码，未直接复制原仓库源码。

## 架构

输入银行 Excel 流水 → BankAdapter（识别模板、映射与转码） → 统一流水模型 v1 → DataProcessor（质量校验） → MetricsCalculator（通用计算） → risk_rules（风险线索） → ReportGenerator（Word / Markdown / JSON）。

* bank_adapters.py：BankAdapter 抽象基类、ABC / ICBC / CCB 初始模板、GENERIC 适配器、自定义 JSON 模板、多个 Excel 工作表导入
* statement_schema.py：STANDARD_COLUMNS，所有适配器必须满足的标准流水字段约定
* data_processor.py：仅消费标准列、去歧义、账号/币种隔离、缺失和重复标记、可验证的余额连续性
* metrics_calculator.py：收支趋势、候选经营性流水、偿债支出、日均余额、对手方、季节性
* risk_rules.py：大额、整数金额、回流、集中度、波动、重复记录等待复核线索
* report_generator.py：证据明细 JSON、DOCX 与 Markdown 报告

银行模板仅是常见 Excel 字段映射示例，不代表已兼容某银行所有网银版本，实际接入需脱敏样本验收。

## 安装和使用

~~~bash
pip install -r requirements.txt

# 自动根据文件名、文件前导信息识别银行；表头用通用别名解析
python main.py abc.xlsx --company "测试企业" --output output

# 同一客户不同银行/账户流水合并分析
python main.py abc.xlsx icbc.xlsx --company "测试企业" --output output

# 显式使用特定适配器
python main.py any.xlsx --bank ICBC --company "测试企业"

# 新银行无需更改分析代码，通过 JSON 定义字段映射
python main.py other.xlsx --bank CUSTOM --profile config/example_bank.json --company "测试企业"
~~~

输出 analysis.docx、analysis.md、analysis.json。JSON 中包含源文件、工作表、原始 Excel 行号和命中的规则编号，方便人工复核。

## 标准流水接口

详见 [标准数据模型](docs/standard_model.md)。强制字段包括：银行编码、账号、交易日期、贷方发生额、借方发生额、余额、交易用途、source_file、source_sheet、source_row。金额单位为人民币元，贷方为入账、借方为出账；具体银行如果用单一金额列，必须提供已核实的收支方向映射或显式 signed 配置。

## 限制与边界

* 仅支持 Excel（xls/xlsx），暂不含 PDF/OCR，不对 Excel 文件真伪作保证。
* 支持 ABC/ICBC/CCB 的常见字段示例和自定义模板，并未针对各行真实导出格式做端到端验收。
* 默认严格限制人民币 CNY，外币数据会停止分析，避免不同币种直接相加。
* 无法自动识别银行时使用 GENERIC 通用表头映射，但按源文件、工作表生成临时银行标识，不把来源不明的同号账户误认为同一账户。
* 同日多笔交易缺少明确时间顺序时，余额连续性只检查可以确认的交易对；缺失的核验记录在报告中。
* 经营性收支是摘要关键词得到的**候选金额**，需要人工核对真实经营活动。
* 风险规则表示疑点，不代表交易虚假；不做报表、税票、合同等外部交叉验证。
* 安全、审计、脱敏、权限与生产运维尚待完善。

## 测试

~~~bash
pip install -r requirements.txt pytest
pytest -q
~~~

注意：上游 README 虽注明 MIT，但其仓库当时没有独立 LICENSE 文件；复用上游代码前应进一步确认许可。
