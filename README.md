# 银行信贷流水审核辅助分析 (Bank Statement Analysis)

基于 [weiwill88/bank-statement-analysis](https://github.com/weiwill88/bank-statement-analysis) 的模块分层思路扩展，保留 Python/pandas、Excel 导入、Word/Markdown 输出的工作方式。**本仓库是独立改进实现，不保证原项目各函数接口完全兼容。**

## 已实现范围

- 农行对公 Excel 常见字段映射、账号提取、日期金额标准化、无效记录统计、重复交易标记
- 按账户核查余额连续性（同日无序流水跳过不可判定部分）、保留源行号
- 流入流出、候选经营性流入/流出、偿债支出、日均余额（月内无交易日照常纳入）
- 月度趋势、变异系数、经营流入活跃月份比例、跨年季度季节性统计
- 交易对手 TOP20、用途关键词分类与无法分类提示
- 大额/整数/疑似重复、同一对手短期近额回流、集中度、现金流波动等线索
- Word、Markdown、JSON 可追溯报告

## 运行

```bash
pip install -r requirements.txt
python main.py 流水文件.xlsx --company "样例企业" --output output
python main.py 流水文件.xlsx --company "样例企业" --large-amount 1000000
```

生成 `analysis.docx`、`analysis.md` 和 `analysis.json`。JSON 包含源行号和证据字段。

## 重要限制

1. 当前主要适配农行对公 Excel；未实现 PDF/OCR 和跨行模板识别。
2. 经营性流水仅为**规则候选值**，关键词未命中的交易需人工复核，不能直接认定为真实营业收入。
3. 余额验证依赖交易先后顺序；同日无稳定流水号时跳过相邻同日检查，无法从 Excel 排除篡改。
4. 风险线索只表示需要核查，不直接判断欺诈、关联交易或实际资金空转。
5. 不做财务报表、发票、税务等外部交叉验证；不自动作出授信审批结论。
6. 仅提供静态源代码测试，真实银行模板及规则效果需以脱敏样本验证；生产使用前应补齐权限、审计、脱敏与安全评估。

## 结构

`data_processor.py` 解析与质量控制；`metrics_calculator.py` 指标；`risk_rules.py` 规则；`report_generator.py` 输出；`main.py` CLI。

## 开发

```bash
pip install pytest
pytest -q
```

引用开源项目时请核验上游实际许可证，当前上游 README 中虽然标示 MIT，但仓库未见 LICENSE 文件；本实现不直接复制上游源码文本。
