"""流水标准化与基础校验。保留原项目 DataProcessor 的接口。"""
from pathlib import Path
import pandas as pd
import numpy as np

ALIASES = {
 "交易日期":["交易日期","日期","交易时间","date"],
 "账号":["账号","本方账号","银行账号","账户号码"],
 "开户行":["开户行","开户银行","银行名称"],
 "贷方发生额":["贷方发生额","贷方","收入","credit"],
 "借方发生额":["借方发生额","借方","支出","debit"],
 "余额":["余额","balance"],
 "对方账号":["对方账号","对方账户","对方帐号","对方帐户"],
 "对方户名":["对方户名","对方名称","对方单位"],
 "对方开户行":["对方开户行","对方行号","对方银行"],
 "交易用途":["交易用途","用途","摘要","备注"],
 "交易流水号":["交易流水号","交易序号","流水号","交易编号","凭证号"],
 "交易时间":["交易时刻","记账时间","发生时间"]
}
REQUIRED = ["交易日期","贷方发生额","借方发生额","余额","交易用途"]

class DataProcessor:
 def __init__(self):
  self.account_info={}
  self.raw_data=None
  self.processed_data=None
  self.quality_report={}

 def read_excel(self,path):
  path=Path(path)
  if path.suffix.lower() not in (".xlsx",".xls"):
   raise ValueError("仅支持 Excel 文件")
  preview=pd.read_excel(path,header=None,dtype=str)
  header=None
  for idx,row in preview.head(50).iterrows():
   if any(str(c).strip() in ALIASES["交易日期"] for c in row):
    header=idx;break
  if header is None: raise ValueError("无法定位流水表头")
  pre=preview.iloc[:header].fillna("")
  for _,row in pre.iterrows():
   line=" ".join(str(v) for v in row if str(v).strip())
   for target,keys in {"账号":["账号:","账号：","账户号码:"],"户名":["户名:","户名：","公司名称:"],"开户行":["开户行:","开户行："]}.items():
    for key in keys:
     if key in line and target not in self.account_info:
      self.account_info[target]=line.split(key,1)[1].strip().split(" ")[0]
  self.raw_data=pd.read_excel(path,header=header,dtype=str)

 def standardize_columns(self):
  rename={}
  for col in self.raw_data.columns:
   for standard,names in ALIASES.items():
    if str(col).strip().lower() in [s.lower() for s in names]:
     rename[col]=standard;break
  self.raw_data=self.raw_data.rename(columns=rename)
  if len(set(self.raw_data.columns))!=len(self.raw_data.columns):
   raise ValueError("映射后出现重复字段，请检查流水表头")
  missing=[c for c in REQUIRED if c not in self.raw_data]
  if missing: raise ValueError("缺少必要字段: "+",".join(missing))
  if "账号" not in self.raw_data and self.account_info.get("账号"):
   self.raw_data["账号"]=self.account_info["账号"]
  if "账号" not in self.raw_data: self.raw_data["账号"]="UNSPECIFIED_ACCOUNT"

 def clean_data(self):
  d=self.raw_data.copy()
  self.quality_report={"original_count":int(len(d)),"invalid_dates":0,"invalid_amounts":0,"duplicate_rows":0,"missing_purpose":0,"missing_counterparty":0,"limitations":[]}
  d["source_row"]=np.arange(len(d))+2
  d["交易日期"]=pd.to_datetime(d["交易日期"],errors="coerce")
  self.quality_report["invalid_dates"]=int(d["交易日期"].isna().sum())
  d=d.dropna(subset=["交易日期"]).copy()
  for col in ["贷方发生额","借方发生额","余额"]:
   v=d[col].astype("string").str.replace(",","",regex=False).str.replace("￥","",regex=False).str.strip()
   if col!="余额": v=v.fillna("0").replace("", "0")
   d[col]=pd.to_numeric(v,errors="coerce")
   self.quality_report["invalid_amounts"]+=int(d[col].isna().sum())
  bad=d[["贷方发生额","借方发生额","余额"]].isna().any(axis=1)
  self.quality_report["invalid_amounts_rows"]=int(bad.sum())
  d=d.loc[~bad].copy()
  for col in ["对方账号","对方户名","对方开户行","交易用途","开户行","交易流水号","交易时间"]:
   if col not in d: d[col]=""
   d[col]=d[col].fillna("").astype(str).str.strip()
  d["账号"]=d["账号"].fillna("UNSPECIFIED_ACCOUNT").astype(str).str.strip()
  self.quality_report["missing_purpose"]=int(d["交易用途"].eq("").sum())
  self.quality_report["missing_counterparty"]=int(d["对方户名"].eq("").sum())
  keys=["账号","交易日期","贷方发生额","借方发生额","余额","对方账号","对方户名","交易用途"]
  if d["交易流水号"].ne("").any(): keys=["账号","交易流水号"]
  duplicated=d.duplicated(subset=keys,keep="first")
  self.quality_report["duplicate_rows"]=int(duplicated.sum())
  # 不擅自删除重复交易；只在报告中标记，以避免合法同额交易丢失
  d["疑似重复"]=duplicated
  sort_cols=["账号","交易日期"]
  if d["交易时间"].ne("").all(): sort_cols.append("交易时间")
  if d["交易流水号"].ne("").all(): sort_cols.append("交易流水号")
  else: self.quality_report["limitations"].append("同日交易缺少稳定序号，余额连续性可能无法可靠判定")
  sort_cols.append("source_row")
  self.processed_data=d.sort_values(sort_cols,kind="stable").reset_index(drop=True)
  self.quality_report["valid_count"]=int(len(d))

 def check_data_quality(self):
  d=self.processed_data
  exceptions=[]
  for account,g in d.groupby("账号",sort=False):
   g=g.reset_index(drop=True)
   for i in range(1,len(g)):
    prev,cur=g.iloc[i-1],g.iloc[i]
    if prev["交易日期"]==cur["交易日期"] and not (cur["交易时间"] and prev["交易时间"] or cur["交易流水号"] and prev["交易流水号"]):
     continue
    expected=prev["余额"]+cur["贷方发生额"]-cur["借方发生额"]
    if abs(cur["余额"]-expected)>0.011:
     exceptions.append({"account":account,"row":int(cur["source_row"]),"date":str(cur["交易日期"].date()),"expected":round(float(expected),2),"actual":round(float(cur["余额"]),2),"delta":round(float(cur["余额"]-expected),2)})
  self.quality_report["balance_exceptions"]=exceptions
  self.quality_report["balance_error_count"]=len(exceptions)
  return self.quality_report

 def process_all(self,path):
  self.read_excel(path)
  self.standardize_columns()
  self.clean_data()
  return self.processed_data,self.check_data_quality()
