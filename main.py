"""命令行入口，兼容原有处理器/指标/报告模块职责。"""
import argparse
from pathlib import Path
from data_processor import DataProcessor
from metrics_calculator import MetricsCalculator
from risk_rules import RuleConfig,find_risks
from report_generator import ReportGenerator

def analyze(file_path,company_name,output_dir,config=None):
 processor=DataProcessor()
 data,quality=processor.process_all(file_path)
 metrics=MetricsCalculator(data).calculate_all_metrics()
 findings=find_risks(data,metrics,config)
 return ReportGenerator().generate_report(metrics,company_name,output_dir,quality,findings)

if __name__=="__main__":
 parser=argparse.ArgumentParser(description="银行信贷流水审核辅助分析")
 parser.add_argument("input",help="对公流水 xlsx/xls")
 parser.add_argument("--company",required=True,help="企业名称")
 parser.add_argument("--output",default="output",help="报告目录")
 parser.add_argument("--large-amount",type=float,default=500000,help="大额交易线索阈值（元）")
 args=parser.parse_args()
 print(analyze(args.input,args.company,args.output,RuleConfig(large_transaction=args.large_amount)))
