"""可解释且可追溯的信贷流水异常线索，命中不构成欺诈或真实交易认定。"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class RuleConfig:
    large_transaction: float = 500000.0
    round_amount_min: float = 10000.0
    round_amount_multiple: float = 10000.0
    rapid_return_days: int = 3
    rapid_return_min_amount: float = 50000.0
    rapid_return_tolerance: float = 0.02
    counterparty_concentration: float = 0.5
    monthly_cv_threshold: float = 1.0


def find_risks(data, metrics, config=None):
    cfg = config or RuleConfig()
    d = metrics["classified_data"]
    findings = []

    def source_key(row):
        return (str(row.get("银行编码", "")), str(row.get("账号", "")),
                str(row.get("source_file", "")), str(row.get("source_sheet", "")),
                int(row["source_row"]))

    def add(code, level, reason, txns=None, **evidence):
        txns = txns or []
        refs = [{"file": str(r.get("source_file", "")),
                 "sheet": str(r.get("source_sheet", "")),
                 "row": int(r["source_row"]),
                 "account": str(r.get("账号", "")),
                 "bank": str(r.get("银行编码", "")),
                 "page": int(r.get("source_page", 0) or 0),
                 "method": str(r.get("extraction_method", ""))}
                for r in txns]
        findings.append({
            "code": code, "level": level, "reason": reason,
            "source_rows": [int(r["source_row"]) for r in txns],
            "source_refs": refs, "evidence": evidence,
        })

    for _, row in d.iterrows():
        amount = max(float(row["贷方发生额"]), float(row["借方发生额"]))
        if amount >= cfg.large_transaction:
            add("LARGE_TXN", "MEDIUM", "大额交易，需核查背景", [row],
                amount=amount, date=str(row["交易日期"].date()))
        if (cfg.round_amount_multiple > 0 and amount >= cfg.round_amount_min
                and abs(amount % cfg.round_amount_multiple) < 0.001):
            add("ROUND_TXN", "LOW", "较大整数金额交易，需结合业务判断", [row], amount=amount)
        if row["疑似重复"]:
            add("POSSIBLE_DUPLICATE", "MEDIUM", "疑似重复流水记录", [row], amount=amount)
        if row["交易分类"] == "待人工核实" and amount > 0:
            add("UNKNOWN_PURPOSE", "LOW", "交易用途无法归类", [row], amount=amount)
        if row["对方账号"] and row["对方账号"] == row["账号"]:
            add("SAME_ACCOUNT", "MEDIUM", "对方账号与本方账号相同", [row], amount=amount)

    # 同本方账户、同对手账户、相近金额的短期反向资金往来。
    # 未识别的交易性质仍可能是合理退款，需人工复核。
    group_keys = [key for key in ("银行编码", "账号", "币种", "对方账号")
                  if key in d.columns]
    counterparties = d[d["对方账号"].ne("")]
    for _, group in counterparties.groupby(group_keys, dropna=False):
        group = group.sort_values(["交易日期", "source_row"])
        incoming = group[group["贷方发生额"] >= cfg.rapid_return_min_amount]
        outgoing = group[group["借方发生额"] >= cfg.rapid_return_min_amount]
        used = set()
        for _, credit in incoming.iterrows():
            key = source_key(credit)
            if key in used:
                continue
            candidates = outgoing[
                (outgoing["交易日期"] >= credit["交易日期"])
                & (outgoing["交易日期"] <= credit["交易日期"]
                   + pd.Timedelta(days=cfg.rapid_return_days))
                & ((outgoing["借方发生额"] - credit["贷方发生额"]).abs()
                   <= credit["贷方发生额"] * cfg.rapid_return_tolerance)
            ]
            candidates = candidates[
                candidates.apply(lambda r: source_key(r) not in used, axis=1)
            ]
            if candidates.empty:
                continue
            opposite = candidates.iloc[0]
            if source_key(opposite) == key:
                continue
            add("RAPID_RETURN", "HIGH", "同一交易对手短期近额资金回流",
                [credit, opposite], party=str(credit["对方账号"]),
                amount=float(credit["贷方发生额"]),
                days=int((opposite["交易日期"] - credit["交易日期"]).days))
            used.update((key, source_key(opposite)))

    total = float(metrics["cash_flow"]["inflow"])
    if total > 0:
        for item in metrics["counterparties"]:
            if item["inflow"] > 0 and item["inflow"] / total >= cfg.counterparty_concentration:
                add("CONCENTRATION", "MEDIUM", "单一对手方流入集中度高",
                    name=item["name"], account=item["account"],
                    ratio=round(item["inflow"] / total, 4))
    cv = metrics["stability"]["operating_inflow_cv"]
    if cv is not None and cv > cfg.monthly_cv_threshold:
        add("HIGH_VOLATILITY", "MEDIUM", "候选经营性流入月度波动较大", cv=cv)
    return findings
