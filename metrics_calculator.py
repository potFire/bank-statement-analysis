"""现金流、经营实质、稳定性与季节性指标。"""
import pandas as pd
import numpy as np

CATEGORIES=[
 ("内部往来",["内部","调拨","划转","同名","内部转账"]),
 ("融资借入",["贷款发放","借款到账","融资款","贷款入账","放款"]),
 ("融资偿还",["还贷","贷款归还","偿还本金","归还借款","贷款扣款"]),
 ("理财投资",["理财","基金","证券","赎回","投资"]),
 ("退款冲正",["退款","冲正","退回","返还"]),
 ("政府补助",["补贴","补助","财政拨款"]),
 ("薪资社保",["工资","薪酬","社保","公积金","劳务费"]),
 ("税费",["缴税","税款","税费","增值税","所得税"]),
 ("租赁",["房租","租金","租赁费"]),
 ("水电能源",["电费","水费","燃气费","能源费"]),
 ("现金交易",["存现","取现","现金"]),
 ("经营往来",["货款","销售","采购","订单","服务费","工程款","合同款"])
]
NON_OPERATING={"内部往来","融资借入","融资偿还","理财投资","退款冲正","政府补助","现金交易"}

def classify(purpose):
 p=str(purpose).lower()
 for category,words in CATEGORIES:
  if any(word.lower() in p for word in words):return category
 return "待人工核实"

class MetricsCalculator:
 def __init__(self,processed_data):
  self.data=processed_data.copy()
 def calculate_all_metrics(self):
  d=self.data
  d["交易分类"]=d["交易用途"].map(classify)
  d["是否经营性"]=~d["交易分类"].isin(NON_OPERATING|{"待人工核实"})
  d["month"]=d["交易日期"].dt.to_period("M")
  d["year"]=d["交易日期"].dt.year
  d["quarter"]=d["交易日期"].dt.quarter
  total_in=float(d["贷方发生额"].sum());total_out=float(d["借方发生额"].sum())
  op=d[d["是否经营性"]]
  op_in=float(op["贷方发生额"].sum());op_out=float(op["借方发生额"].sum())
  unknown=d[d["交易分类"]=="待人工核实"]
  monthly=d.groupby("month").agg(inflow=("贷方发生额","sum"),outflow=("借方发生额","sum"),transactions=("source_row","size"))
  monthly=monthly.reindex(pd.period_range(d["month"].min(),d["month"].max(),freq="M"),fill_value=0) if len(d) else monthly
  monthly["net"]=monthly.inflow-monthly.outflow
  monthly_rows=[{"month":str(t),"inflow":round(float(r.inflow),2),"outflow":round(float(r.outflow),2),"net":round(float(r.net),2),"count":int(r.transactions)} for t,r in monthly.iterrows()]
  op_month=op.groupby("month")["贷方发生额"].sum().reindex(monthly.index,fill_value=0)
  cv=float(op_month.std(ddof=0)/op_month.mean()) if len(op_month) and op_month.mean()>0 else None
  # 用观察区间内的全部自然日计算日均，不将无交易日排除
  account_daily=[]
  if len(d):
   start=d["交易日期"].min().normalize();end=d["交易日期"].max().normalize()
   calendar=pd.date_range(start,end,freq="D")
   for account,g in d.groupby("账号"):
    last=g.groupby(g["交易日期"].dt.normalize())["余额"].last().sort_index()
    balance_series=last.reindex(calendar).ffill()
    # 未知区间前期余额，依首笔交易反推，且仅用于该首笔交易之前的自然日
    first=g.iloc[0]
    initial=float(first["余额"]-first["贷方发生额"]+first["借方发生额"])
    balance_series=balance_series.fillna(initial)
    account_daily.append(balance_series)
   aggregate=sum(account_daily)
   daily_avg=round(float(aggregate.mean()),2)
  else: daily_avg=0.0
  party=d.groupby(["对方账号","对方户名"],dropna=False).agg(inflow=("贷方发生额","sum"),outflow=("借方发生额","sum"),count=("source_row","size")).reset_index()
  party["gross"]=party.inflow+party.outflow
  party=party.sort_values("gross",ascending=False)
  top=[{"account":str(r["对方账号"]),"name":str(r["对方户名"]),"inflow":round(float(r.inflow),2),"outflow":round(float(r.outflow),2),"count":int(r["count"])} for _,r in party.head(20).iterrows()]
  cat=d.groupby("交易分类")[["贷方发生额","借方发生额"]].sum()
  category=[{"category":idx,"inflow":round(float(r["贷方发生额"]),2),"outflow":round(float(r["借方发生额"]),2)} for idx,r in cat.iterrows()]
  repaid=d[d["交易分类"]=="融资偿还"]
  seasonal=[]
  if d["year"].nunique()>=2 and len(monthly)>=18:
   quarter=op.groupby(["year","quarter"])["贷方发生额"].sum()
   for q in range(1,5):
    values=[float(v) for (year,quarter_no),v in quarter.items() if quarter_no==q]
    if len(values)>=2:seasonal.append({"quarter":q,"mean_operating_inflow":round(float(np.mean(values)),2),"observations":len(values)})
  return {"cash_flow":{"inflow":round(total_in,2),"outflow":round(total_out,2),"net":round(total_in-total_out,2),"operating_inflow_candidate":round(op_in,2),"operating_outflow_candidate":round(op_out,2),"operating_net_candidate":round(op_in-op_out,2),"repayment_outflow":round(float(repaid["借方发生额"].sum()),2),"repayment_to_operating_inflow":round(float(repaid["借方发生额"].sum()/op_in),4) if op_in else None,"unclassified_inflow":round(float(unknown["贷方发生额"].sum()),2),"daily_avg_balance":daily_avg},
   "monthly":monthly_rows,"stability":{"operating_inflow_cv":round(cv,4) if cv is not None else None,"active_inflow_month_ratio":round(float((op_month>0).mean()),4) if len(op_month) else None,"seasonality_quarters":seasonal,"seasonality_sufficient":len(seasonal)>=4},
   "counterparties":top,"categories":category,"classified_data":d}
