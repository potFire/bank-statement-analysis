import pandas as pd
from metrics_calculator import MetricsCalculator
from risk_rules import RuleConfig,find_risks
from data_processor import DataProcessor

def sample():
 d=pd.DataFrame([
  ["A","2026-01-05",100000,0,100000,"X","客户甲","货款",2],
  ["A","2026-01-06",0,100000,0,"X","客户甲","采购",3],
  ["A","2026-02-05",60000,0,60000,"Y","客户乙","货款",4],
 ],columns=["账号","交易日期","贷方发生额","借方发生额","余额","对方账号","对方户名","交易用途","source_row"])
 d["交易日期"]=pd.to_datetime(d["交易日期"])
 for c in ("对方开户行","开户行","交易流水号","交易时间"):d[c]=""
 d["疑似重复"]=False
 return d

def test_metrics_and_risk():
 d=sample();m=MetricsCalculator(d).calculate_all_metrics()
 assert m["cash_flow"]["inflow"]==160000
 assert m["cash_flow"]["net"]==60000
 assert m["cash_flow"]["daily_avg_balance"]>0
 assert any(x["code"]=="RAPID_RETURN" for x in find_risks(d,m,RuleConfig()))

def test_data_quality(tmp_path):
 p=tmp_path/"sample.xlsx"
 df=sample().drop(columns=["source_row","疑似重复"])
 df.to_excel(p,index=False)
 data,q=DataProcessor().process_all(p)
 assert q["balance_error_count"]==0
 assert q["valid_count"]==3

def test_per_account_balance(tmp_path):
 p=tmp_path/"accounts.xlsx"
 rows=[
  ["2026-01-01","A",100,0,100,"货款","X","t1"],
  ["2026-01-02","B",300,0,300,"货款","Y","t2"],
  ["2026-01-03","A",0,20,80,"采购","X","t3"]
 ]
 pd.DataFrame(rows,columns=["交易日期","账号","贷方发生额","借方发生额","余额","交易用途","对方户名","交易流水号"]).to_excel(p,index=False)
 _,q=DataProcessor().process_all(p)
 assert q["balance_error_count"]==0
