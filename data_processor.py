"""统一数据模型的质量检查层；银行字段映射在 bank_adapters.py 完成。"""
import re
import numpy as np
import pandas as pd
from document_ingest import UniversalStatementImporter
from statement_schema import SCHEMA_VERSION, STANDARD_COLUMNS, MONEY_COLUMNS, validate_standard_frame


def parse_date(value):
    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT
    value = str(value).strip()
    if re.fullmatch(r"\d{8}", value):
        return pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    if re.fullmatch(r"\d{1,5}", value) and 1 <= int(value) <= 80000:
        return pd.to_datetime(int(value), unit="D", origin="1899-12-30", errors="coerce")
    value = value.replace("年", "-").replace("月", "-").replace("日", "")
    return pd.to_datetime(value, errors="coerce")


def parse_timestamp(date, time):
    """无法获知真实日内顺序时返回 NaT，防止错误的余额连续性判断。"""
    if pd.isna(date):
        return pd.NaT
    clock = "" if pd.isna(time) else str(time).strip()
    if not clock:
        return date if date.hour or date.minute or date.second else pd.NaT
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", clock):
        return pd.to_datetime(f"{date.date()} {clock}", errors="coerce")
    return pd.to_datetime(clock, errors="coerce")


class DataProcessor:
    def __init__(self, bank="auto", profile_path=None, llm_client=None, max_pages=200):
        self.importer = UniversalStatementImporter(bank, profile_path, llm_client, max_pages)
        self.raw_data = None
        self.processed_data = None
        self.account_info = {}
        self.import_sources = []
        self.quality_report = {}

    def read_excel(self, paths):
        self.raw_data, self.import_sources = self.importer.read(paths)
        self.account_info = self.import_sources[0].get("account_info", {}) if len(self.import_sources) == 1 else {}
        return self.raw_data

    def standardize_columns(self):
        validate_standard_frame(self.raw_data)
        return self.raw_data

    def clean_data(self):
        validate_standard_frame(self.raw_data)
        d = self.raw_data.copy()
        q = {
            "schema_version": SCHEMA_VERSION, "sources": self.import_sources,
            "original_count": len(d), "invalid_dates": 0,
            "review_required_records": int(d["review_required"].fillna(False).astype(bool).sum()),
            "llm_extracted_records": int(d["extraction_method"].astype(str).str.startswith("llm_").sum()),
            "recognition_score_min": float(d["recognition_score"].min()),
            "invalid_amounts_rows": 0, "duplicate_rows": 0,
            "missing_purpose": 0, "missing_counterparty": 0,
            "rejected_locations": [], "limitations": [
                "候选经营收支需人工判定交易性质",
                "同日交易无可验证顺序时，部分余额连续性检查会跳过",
            ],
        }
        currencies = d["币种"].fillna("CNY").astype(str).str.strip().str.upper()
        currencies = currencies.replace({"": "CNY", "人民币": "CNY", "元": "CNY", "RMB": "CNY"})
        unsupported = sorted(set(currencies) - {"CNY"})
        if unsupported:
            raise ValueError("当前不支持非人民币流水及汇率换算: " + ",".join(unsupported))
        d["币种"] = currencies
        d["交易日期"] = d["交易日期"].map(parse_date)
        invalid_date = d["交易日期"].isna()
        q["invalid_dates"] = int(invalid_date.sum())
        for col in MONEY_COLUMNS:
            value = d[col].astype("string").str.replace(",", "", regex=False)
            value = value.str.replace("￥", "", regex=False).str.replace("¥", "", regex=False).str.strip()
            value = value.str.replace(r"^\((.+)\)$", r"-\1", regex=True)
            if col != "余额":
                value = value.fillna("0").replace("", "0")
            d[col] = pd.to_numeric(value, errors="coerce")
        invalid_money = (
            d[list(MONEY_COLUMNS)].isna().any(axis=1)
            | (d["贷方发生额"] < 0)
            | (d["借方发生额"] < 0)
            | ((d["贷方发生额"] > 0) & (d["借方发生额"] > 0))
        )
        q["invalid_amounts_rows"] = int(invalid_money.sum())
        rejected = d[invalid_date | invalid_money]
        q["rejected_locations"] = [
            {"file": r.source_file, "sheet": r.source_sheet, "row": int(r.source_row)}
            for r in rejected.head(200).itertuples()
        ]
        d = d.loc[~(invalid_date | invalid_money)].copy()
        if d.empty:
            raise ValueError("没有可分析的有效交易数据")
        for col in ("银行编码", "银行名称", "账号", "账户户名", "开户行",
                    "交易时间", "交易流水号", "对方账号", "对方户名", "对方开户行",
                    "交易用途", "source_file", "source_sheet"):
            d[col] = d[col].fillna("").astype(str).str.strip()
        q["missing_purpose"] = int(d["交易用途"].eq("").sum())
        q["missing_counterparty"] = int(d["对方户名"].eq("").sum())
        duplicate_fields = ["银行编码", "账号", "币种", "交易日期", "贷方发生额",
                            "借方发生额", "余额", "对方账号", "对方户名", "交易用途"]
        has_id = d["交易流水号"].ne("")
        d["疑似重复"] = False
        d.loc[has_id, "疑似重复"] = d[has_id].duplicated(
            subset=["银行编码", "账号", "币种", "交易流水号"], keep="first"
        )
        d.loc[~has_id, "疑似重复"] = d[~has_id].duplicated(
            subset=duplicate_fields, keep="first"
        )
        q["duplicate_rows"] = int(d["疑似重复"].sum())
        d["_交易日"] = d["交易日期"].dt.normalize()
        d["_交易排序时间"] = [parse_timestamp(date, clock) for date, clock
                             in zip(d["交易日期"], d["交易时间"])]
        d = d.sort_values(
            ["银行编码", "账号", "币种", "_交易日", "_交易排序时间",
             "source_file", "source_sheet", "source_row"],
            kind="stable", na_position="last"
        ).reset_index(drop=True)
        self.processed_data = d
        q["valid_count"] = int(len(d))
        self.quality_report = q
        return d

    def check_data_quality(self):
        errors, checked, skipped = [], 0, 0
        for (bank, account, currency), group in self.processed_data.groupby(
                ["银行编码", "账号", "币种"], sort=False):
            g = group.reset_index(drop=True)
            if str(account).startswith("UNSPECIFIED:"):
                skipped += max(0, len(g) - 1)
                continue
            day_counts = g.groupby("_交易日").size()
            ordered_day = {
                day: bool(part["_交易排序时间"].notna().all()
                          and part["_交易排序时间"].is_unique)
                for day, part in g.groupby("_交易日")
            }
            for i in range(1, len(g)):
                prev, cur = g.iloc[i - 1], g.iloc[i]
                pd_, cd_ = prev["_交易日"], cur["_交易日"]
                # 前一日或本日存在同日多笔且缺序号，不能确定跨日比较的端点。
                if (day_counts[pd_] > 1 and not ordered_day[pd_]) or (
                        day_counts[cd_] > 1 and not ordered_day[cd_]):
                    skipped += 1
                    continue
                checked += 1
                expected = prev["余额"] + cur["贷方发生额"] - cur["借方发生额"]
                if abs(float(cur["余额"] - expected)) > 0.011:
                    errors.append({
                        "bank": bank, "account": account, "currency": currency,
                        "source_file": cur["source_file"], "source_sheet": cur["source_sheet"],
                        "row": int(cur["source_row"]), "date": str(cur["_交易日"].date()),
                        "expected": round(float(expected), 2),
                        "actual": round(float(cur["余额"]), 2),
                        "delta": round(float(cur["余额"] - expected), 2),
                    })
        self.quality_report.update({
            "balance_exceptions": errors, "balance_error_count": len(errors),
            "balance_checked_pairs": checked, "balance_unverifiable_pairs": skipped,
        })
        return self.quality_report

    def process_standard_frame(self, frame, sources=None):
        """已由人工复核的标准表格从这里进入相同的质量/指标分析流程。"""
        self.raw_data = frame.copy()
        self.import_sources = sources or [{"source_type": "reviewed_workbook", "review_required": False}]
        self.standardize_columns()
        self.clean_data()
        if self.quality_report["invalid_dates"] or self.quality_report["invalid_amounts_rows"]:
            raise ValueError("人工复核表仍存在无法解析的交易日期或金额，请修正后重新导入")
        self.quality_report["review_status"] = "user_attested_approved"
        self.quality_report["review_required_records"] = 0
        return self.processed_data, self.check_data_quality()

    def process_all(self, paths):
        self.read_excel(paths)
        self.standardize_columns()
        self.clean_data()
        return self.processed_data, self.check_data_quality()
