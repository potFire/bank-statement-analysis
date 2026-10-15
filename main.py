"""银行流水审核命令行：多格式识别 -> 标准模型 -> 人工复核 -> 分析。"""
import argparse
from pathlib import Path

from data_processor import DataProcessor
from metrics_calculator import MetricsCalculator
from review_workflow import export_review_workbook, read_approved_workbook
from risk_rules import RuleConfig, find_risks
from report_generator import ReportGenerator
from vision_llm import LLMSettings, VisionLLM


def analyze(file_path, company_name, output_dir, config=None, bank="auto", profile_path=None,
            llm_client=None, max_pages=200, review_only=False,
            allow_unreviewed=False, reviewed_xlsx=None):
    processor = DataProcessor(
        bank=bank, profile_path=profile_path, llm_client=llm_client, max_pages=max_pages
    )
    if reviewed_xlsx:
        if file_path:
            raise ValueError("--reviewed-xlsx 不能与输入的原始流水文件同时提供")
        standard = read_approved_workbook(reviewed_xlsx)
        data, quality = processor.process_standard_frame(standard)
    else:
        if not file_path:
            raise ValueError("请输入银行流水文件，或提供 --reviewed-xlsx")
        data, quality = processor.process_all(file_path)
        needs_review = quality["review_required_records"] > 0
        if needs_review or review_only:
            review_path = export_review_workbook(data, quality["sources"], output_dir)
            if review_only or (needs_review and not allow_unreviewed):
                return str(review_path)
        quality["review_status"] = (
            "UNREVIEWED_DRAFT" if needs_review else "excel_rule_processed"
        )
    metrics = MetricsCalculator(data).calculate_all_metrics()
    findings = find_risks(data, metrics, config)
    return ReportGenerator().generate_report(
        metrics, company_name, output_dir, quality, findings
    )


def main():
    parser = argparse.ArgumentParser(description="银行流水 Excel / PDF / 图片识别与信贷审核辅助分析")
    parser.add_argument("input", nargs="*", help="一个或多个银行流水文件")
    parser.add_argument("--company", required=True, help="企业名称")
    parser.add_argument("--output", default="output", help="结果与复核文件目录")
    parser.add_argument("--bank", default="auto", help="银行模板代码，例如 ABC/ICBC/CCB/GENERIC")
    parser.add_argument("--profile", help="自定义银行 JSON 字段映射模板")
    parser.add_argument("--llm-url", help="OpenAI-compatible chat/completions URL，默认不连接外网")
    parser.add_argument("--llm-model", help="具备视觉能力的本地或指定模型 ID")
    parser.add_argument("--llm-key-env", default="BANK_LLM_API_KEY", help="保存 API Key 的环境变量名")
    parser.add_argument("--allow-remote-llm", action="store_true",
                        help="显式授权将银行流水内容发送到远端 HTTPS 模型")
    parser.add_argument("--max-pages", type=int, default=200,
                        help="单个 PDF 或多页图片最大页数（默认 200）")
    parser.add_argument("--review-only", action="store_true", help="只输出识别复核 Excel 清单")
    parser.add_argument("--reviewed-xlsx", help="人工逐笔确认后的 recognition_review.xlsx")
    parser.add_argument("--allow-unreviewed", action="store_true",
                        help="允许未经人工复核的 PDF/图片识别结果直接生成草稿报告")
    parser.add_argument("--large-amount", type=float, default=500000.0,
                        help="大额交易线索阈值，单位人民币元")
    args = parser.parse_args()
    if args.reviewed_xlsx and args.input:
        parser.error("--reviewed-xlsx 与原始输入文件不能同时使用")
    if not args.reviewed_xlsx and not args.input:
        parser.error("请输入至少一个流水文件，或指定 --reviewed-xlsx")
    if bool(args.llm_url) != bool(args.llm_model):
        parser.error("--llm-url 与 --llm-model 必须同时设置")
    client = VisionLLM(LLMSettings(
        url=args.llm_url, model=args.llm_model, api_key_env=args.llm_key_env,
        allow_remote=args.allow_remote_llm,
    )) if args.llm_url else None
    try:
        result = analyze(
            args.input or None, args.company, args.output,
            RuleConfig(large_transaction=args.large_amount),
            bank=args.bank, profile_path=args.profile,
            llm_client=client, max_pages=args.max_pages,
            review_only=args.review_only, allow_unreviewed=args.allow_unreviewed,
            reviewed_xlsx=args.reviewed_xlsx,
        )
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"识别/校验失败：{exc}\n")
    print(result)


if __name__ == "__main__":
    main()
