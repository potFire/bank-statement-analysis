"""统一流水格式 -> 校验 -> 通用指标 -> 风险规则 -> 报告。"""
import argparse
from data_processor import DataProcessor
from metrics_calculator import MetricsCalculator
from risk_rules import RuleConfig, find_risks
from report_generator import ReportGenerator


def analyze(file_path, company_name, output_dir, config=None, bank="auto", profile_path=None):
    processor = DataProcessor(bank=bank, profile_path=profile_path)
    data, quality = processor.process_all(file_path)
    metrics = MetricsCalculator(data).calculate_all_metrics()
    findings = find_risks(data, metrics, config)
    return ReportGenerator().generate_report(
        metrics, company_name, output_dir, quality, findings
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="银行信贷流水审核辅助分析")
    parser.add_argument("input", nargs="+", help="一个或多个银行对公 Excel 流水 xlsx/xls")
    parser.add_argument("--company", required=True, help="企业名称")
    parser.add_argument("--output", default="output", help="报告目录")
    parser.add_argument("--bank", default="auto",
                        help="银行类型：auto/ABC/ICBC/CCB/GENERIC/自定义模板编码")
    parser.add_argument("--profile", help="自定义银行 JSON 字段映射模板路径")
    parser.add_argument("--large-amount", type=float, default=500000,
                        help="大额交易预警阈值（元）")
    args = parser.parse_args()
    print(analyze(
        args.input, args.company, args.output,
        RuleConfig(large_transaction=args.large_amount),
        bank=args.bank, profile_path=args.profile,
    ))
