"""银行适配器契约：不同输入结构 -> 同一个标准字段集合。"""
import json
import pandas as pd
import pytest
from openpyxl import Workbook
from statement_schema import STANDARD_COLUMNS, validate_standard_frame
from data_processor import DataProcessor
from metrics_calculator import MetricsCalculator
from risk_rules import find_risks


def workbook(path, headers, rows, preface=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "流水"
    for item in preface or []:
        ws.append([item])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_abc_excel_adapter(tmp_path):
    path = workbook(
        tmp_path / "农业银行.xlsx",
        ["交易日期", "贷方发生额", "借方发生额", "余额", "交易用途", "对方户名", "对方账号"],
        [["2026-01-05", 80000, 0, 80000, "货款", "客户甲", "CP01"],
         ["2026-01-06", 0, 20000, 60000, "采购", "客户乙", "CP02"]],
        ["中国农业银行账户流水", "账号：ABC001"],
    )
    data, q = DataProcessor().process_all(path)
    validate_standard_frame(data)
    assert set(STANDARD_COLUMNS).issubset(data.columns)
    assert data["银行编码"].unique().tolist() == ["ABC"]
    assert data["账号"].unique().tolist() == ["ABC001"]
    assert q["sources"][0]["adapter"] == "ABC"
    assert data["source_row"].tolist() == [4, 5]
    assert q["balance_checked_pairs"] == 1
    assert q["balance_error_count"] == 0


def test_icbc_single_amount_direction(tmp_path):
    path = workbook(
        tmp_path / "工商银行流水.xlsx",
        ["日期", "本方账号", "发生额", "借贷标志", "账户余额", "摘要", "对方名称", "对方账号"],
        [["2026/01/05", "IC001", 60000, "贷", 60000, "货款", "客户甲", "X"],
         ["2026/01/06", "IC001", 10000, "借", 50000, "采购", "客户乙", "Y"]],
    )
    data, q = DataProcessor().process_all(path)
    assert data["银行编码"].unique().tolist() == ["ICBC"]
    assert data["贷方发生额"].tolist() == [60000.0, 0.0]
    assert data["借方发生额"].tolist() == [0.0, 10000.0]
    assert q["balance_error_count"] == 0


def test_ccb_split_aliases(tmp_path):
    path = workbook(
        tmp_path / "建行.xlsx",
        ["交易日期", "账号", "收入金额", "支出金额", "账户余额", "用途"],
        [["2026-01-05", "CC001", 1000, None, 1000, "货款"],
         ["2026-01-06", "CC001", None, 200, 800, "采购"]],
    )
    data, q = DataProcessor().process_all(path)
    assert data["银行编码"].unique().tolist() == ["CCB"]
    assert data["贷方发生额"].tolist() == [1000.0, 0.0]
    assert data["借方发生额"].tolist() == [0.0, 200.0]
    assert q["balance_error_count"] == 0


def test_custom_template_with_nonstandard_headers(tmp_path):
    profile = {
        "code": "CUSTOM", "name": "自定义银行", "amount_mode": "direction",
        "aliases": {
            "交易日期": ["流水发生日"],
            "交易金额": ["动账金额"],
            "收支方向": ["资金方向"],
            "余额": ["结存额"],
            "交易用途": ["业务说明"],
            "账号": ["主体账户"],
        },
        "income_values": ["I"],
        "expense_values": ["O"],
    }
    mapping_file = tmp_path / "custom.json"
    mapping_file.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    path = workbook(
        tmp_path / "other.xlsx",
        ["流水发生日", "主体账户", "动账金额", "资金方向", "结存额", "业务说明"],
        [["2026-01-05", "C1", 500, "I", 500, "销售货款"],
         ["2026-01-06", "C1", 300, "O", 200, "采购"]],
    )
    data, q = DataProcessor(bank="CUSTOM", profile_path=mapping_file).process_all(path)
    assert data["银行编码"].unique().tolist() == ["CUSTOM"]
    assert q["balance_error_count"] == 0


def test_unknown_direction_rejected(tmp_path):
    path = workbook(
        tmp_path / "工商银行.xlsx",
        ["交易日期", "账号", "发生额", "借贷标志", "余额", "摘要"],
        [["2026-01-01", "A", 200, "?", 200, "货款"]],
    )
    with pytest.raises(ValueError, match="无法判断收支方向"):
        DataProcessor().process_all(path)


def test_multi_bank_same_account_isolated(tmp_path):
    # 两家银行恰巧有相同账号；余额检查必须按银行编码隔离。
    abc = workbook(
        tmp_path / "农行.xlsx",
        ["日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要"],
        [["2026-01-01", "SAME", 100, 0, 100, "货款"],
         ["2026-01-02", "SAME", 0, 20, 80, "采购"]],
    )
    icbc = workbook(
        tmp_path / "工行.xlsx",
        ["日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要"],
        [["2026-01-01", "SAME", 900, 0, 900, "货款"],
         ["2026-01-02", "SAME", 0, 100, 800, "采购"]],
    )
    data, q = DataProcessor().process_all([abc, icbc])
    assert len(data) == 4
    assert set(data["银行编码"]) == {"ABC", "ICBC"}
    assert q["balance_checked_pairs"] == 2
    assert q["balance_error_count"] == 0
    stats = MetricsCalculator(data).calculate_all_metrics()
    assert stats["cash_flow"]["inflow"] == 1000
    assert stats["cash_flow"]["daily_avg_balance"] > 0


def test_same_day_unknown_order_skipped(tmp_path):
    path = workbook(
        tmp_path / "农行.xlsx",
        ["日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要"],
        [["2026-01-01", "A", 100, 0, 100, "货款"],
         ["2026-01-01", "A", 0, 30, 70, "采购"],
         ["2026-01-02", "A", 0, 20, 50, "采购"]],
    )
    _, q = DataProcessor().process_all(path)
    assert q["balance_unverifiable_pairs"] == 2
    assert q["balance_checked_pairs"] == 0


def test_non_cny_not_combined(tmp_path):
    path = workbook(
        tmp_path / "农行.xlsx",
        ["日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要", "币种"],
        [["2026-01-01", "A", 100, 0, 100, "货款", "USD"]],
    )
    with pytest.raises(ValueError, match="不支持非人民币"):
        DataProcessor().process_all(path)


def test_lineage_on_risk(tmp_path):
    path = workbook(
        tmp_path / "农行.xlsx",
        ["日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要", "对方账号"],
        [["2026-01-01", "A", 90000, 0, 90000, "货款", "X"],
         ["2026-01-02", "A", 0, 90000, 0, "采购", "X"]],
    )
    data, _ = DataProcessor().process_all(path)
    metrics = MetricsCalculator(data).calculate_all_metrics()
    returns = [x for x in find_risks(data, metrics) if x["code"] == "RAPID_RETURN"]
    assert len(returns) == 1
    assert [(r["file"], r["row"]) for r in returns[0]["source_refs"]] == [
        ("农行.xlsx", 2), ("农行.xlsx", 3),
    ]


def test_generic_files_do_not_merge_accounts(tmp_path):
    first = workbook(
        tmp_path / "one.xlsx",
        ["交易日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要"],
        [["2026-02-01", "A", 100, 0, 100, "销售货款"]],
    )
    second = workbook(
        tmp_path / "two.xlsx",
        ["交易日期", "账号", "贷方发生额", "借方发生额", "余额", "摘要"],
        [["2026-02-01", "A", 500, 0, 500, "销售货款"]],
    )
    data, q = DataProcessor().process_all([first, second])
    assert len(data) == 2
    assert data["银行编码"].nunique() == 2
    assert q["balance_checked_pairs"] == 0
