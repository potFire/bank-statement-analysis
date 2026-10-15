"""跨银行统一流水数据模型（规范 v1）。

适配器必须输出下列字段；金额以元计，入账=贷方发生额，出账=借方发生额；
交易日期为交易发生日期，source_row 为 Excel 工作表中的实际行号。
"""
from typing import Iterable

SCHEMA_VERSION = "1.1"
STANDARD_COLUMNS = (
    "银行编码", "银行名称", "账号", "账户户名", "开户行", "币种",
    "交易日期", "交易时间", "交易流水号",
    "贷方发生额", "借方发生额", "余额",
    "对方账号", "对方户名", "对方开户行", "交易用途",
    "source_file", "source_sheet", "source_row",
    "source_page", "extraction_method", "recognition_score", "review_required",
)
REQUIRED_FOR_ANALYSIS = (
    "银行编码", "账号", "交易日期",
    "贷方发生额", "借方发生额", "余额", "交易用途",
    "source_file", "source_sheet", "source_row",
)
MONEY_COLUMNS = ("贷方发生额", "借方发生额", "余额")


def validate_standard_frame(frame) -> None:
    """结构校验；业务数值校验由 DataProcessor 统一实施。"""
    missing = set(STANDARD_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError("适配器未提供标准字段: " + ", ".join(sorted(missing)))
    duplicates = frame.columns[frame.columns.duplicated()].tolist()
    if duplicates:
        raise ValueError("标准流水包含重复列: " + ", ".join(duplicates))
