"""可复核的异常线索识别。规则命中不代表交易虚假。"""
from dataclasses import dataclass,asdict
import pandas as pd
from metrics_calculator import NON_OPERATING
@dataclass
class RuleConfig:
 large_transaction:float=500000.0
 round_amount_min:float=10000.0
 round_amount_multiple:float=10000.0
 rapid_return_days:int=3
 rapid_return_min_amount:float=50000.0
 rapid_return_tolerance:float=0.02
 counterparty_concentration:float=0.5
 monthly_cv_threshold:float=1.0
 low_balance_days_ratio:float=0.5

def find_risks(data,metrics,config=None):
 cfg=config or RuleConfig()
 d=metrics["classified_data"]
 findings=[]
 def add(code,level,reason,rows=None,**evidence):
  findings.append({"code":code,"level":level,"reason":reason,"source_rows":[int(x) for x in (rows or [])],"evidence":evidence})
 def transaction_amount(r):return max(float(r["贷方发生额"]),float(r["借方发生额"]))
 for _,r in d.iterrows():
  amount=transaction_amount(r)
  if amount>=cfg.large_transaction:
   add("LARGE_TXN","MEDIUM","大额交易，需核查背景",[r.source_row],amount=amount,date=str(r["交易日期"].date()))
  if amount>=cfg.round_amount_min and cfg.round_amount_multiple>0 and abs(amount%cfg.round_amount_multiple)<0.001:
   add("ROUND_TXN","LOW","较大整数金额交易，需结合业务判断",[r.source_row],amount=amount)
  if r["疑似重复"]:
   add("POSSIBLE_DUPLICATE","MEDIUM","疑似重复流水记录",[r.source_row],amount=amount)
  if r["交易分类"]=="待人工核实" and amount>0:
   add("UNKNOWN_PURPOSE","LOW","交易用途无法归类",[r.source_row],amount=amount)
  if r["对方账号"]==r["账号"] and r["对方账号"]:
   add("SAME_ACCOUNT","MEDIUM","对方账号与本方账号相同",[r.source_row],amount=amount)
 # 对手方双向资金往返：同账户、相近金额、指定日数内的反向交易
 for (account,party),g in d[d["对方账号"].ne("")].groupby(["账号","对方账号"]):
  g=g.sort_values(["交易日期","source_row"])
  ins=g[(g["贷方发生额"]>=cfg.rapid_return_min_amount)]
  outs=g[(g["借方发生额"]>=cfg.rapid_return_min_amount)]
  used=set()
  for _,credit in ins.iterrows():
   if int(credit.source_row) in used:continue
   candidates=outs[(outs["交易日期"]>=credit["交易日期"]) & (outs["交易日期"]<=credit["交易日期"]+pd.Timedelta(days=cfg.rapid_return_days))]
   candidates=candidates[(candidates["借方发生额"]-credit["贷方发生额"]).abs()<=credit["贷方发生额"]*cfg.rapid_return_tolerance]
   candidates=candidates[~candidates["source_row"].isin(used)]
   if len(candidates):
    matched=candidates.iloc[0]
    if int(matched.source_row)==int(credit.source_row):continue
    add("RAPID_RETURN","HIGH","同一交易对手短期近额资金回流",[credit.source_row,matched.source_row],party=party,amount=float(credit["贷方发生额"]),days=int((matched["交易日期"]-credit["交易日期"]).days))
    used.update([int(credit.source_row),int(matched.source_row)])
 parties=metrics["counterparties"]
 total=float(metrics["cash_flow"]["inflow"])
 if total>0:
  for party in parties:
   if party["inflow"]>0 and party["inflow"]/total>=cfg.counterparty_concentration:
    add("CONCENTRATION","MEDIUM","单一交易对手流入集中度高",[],name=party["name"],account=party["account"],ratio=round(party["inflow"]/total,4))
 cv=metrics["stability"]["operating_inflow_cv"]
 if cv is not None and cv>cfg.monthly_cv_threshold:
  add("HIGH_VOLATILITY","MEDIUM","候选经营流入月度波动较大",[],cv=cv)
 return findings
