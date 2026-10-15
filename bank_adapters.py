"""多银行 Excel 适配层：模板识别 -> 字段映射 -> 统一流水模型。

增加银行类型只需注册 BankAdapter 子类或提供 JSON 映射配置。
注册的 ABC/ICBC/CCB 是常见字段的初始模板，不等于覆盖银行所有导出版本。
"""
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Dict, Iterable, List, Optional, Sequence, Union
import numpy as np
import pandas as pd
from statement_schema import STANDARD_COLUMNS, validate_standard_frame

# 通用字段别名。按标准字段唯一映射；非标准列保留在原始文件中，不进入分析引擎。
COMMON_ALIASES = {
    "交易日期": ["交易日期", "日期", "发生日期", "记账日期", "入账日期", "交易日", "date"],
    "交易时间": ["交易时间", "发生时间", "交易时刻", "记账时间", "交易日期时间"],
    "账号": ["账号", "本方账号", "银行账号", "账户号码", "本方账户", "账户号", "交易账号", "account", "account_no"],
    "账户户名": ["账户户名", "本方户名", "户名", "账户名称"],
    "开户行": ["开户行", "开户银行", "银行名称", "开户机构"],
    "银行编码": ["银行编码", "bank_code"],
    "币种": ["币种", "货币", "币别", "currency"],
    "贷方发生额": ["贷方发生额", "贷方金额", "贷方", "收入", "收入金额", "转入金额", "存入金额", "credit"],
    "借方发生额": ["借方发生额", "借方金额", "借方", "支出", "支出金额", "转出金额", "支付金额", "debit"],
    "余额": ["余额", "账户余额", "交易后余额", "当前余额", "结余", "balance"],
    "对方账号": ["对方账号", "对方账户", "对方帐号", "对方帐户", "对手账号", "交易对手账号", "交易对方账号"],
    "对方户名": ["对方户名", "对方名称", "对方单位", "对手户名", "交易对手", "交易对方户名", "对方账户名称"],
    "对方开户行": ["对方开户行", "对方行号", "对方银行", "对手开户行"],
    "交易用途": ["交易用途", "用途", "摘要", "备注", "交易摘要", "附言", "交易备注", "purpose", "memo", "description"],
    "交易流水号": ["交易流水号", "交易序号", "流水号", "交易编号", "凭证号", "业务流水号"],
    "交易金额": ["交易金额", "发生金额", "发生额", "金额", "交易发生额"],
    "收支方向": ["收支方向", "借贷标志", "借贷方向", "收付标志", "收入支出标志", "交易方向", "方向"],
}
# 收支方向的取值须按银行原始流水核实，未匹配到的值立即中止解析，避免反向记账。
DEFAULT_INCOME = ("入", "收入", "转入", "存入", "贷", "贷方", "C", "CR", "+", "CREDIT")
DEFAULT_EXPENSE = ("出", "支出", "转出", "取出", "借", "借方", "D", "DR", "-", "DEBIT")


def _normalize_header(name: str) -> str:
    return re.sub(r"\s+", "", str(name)).strip().lower()


@dataclass(frozen=True)
class BankProfile:
    code: str
    name: str
    recognition: Sequence[str] = ()
    aliases: Dict[str, List[str]] = field(default_factory=dict)
    amount_mode: str = "auto"  # auto/split/direction/signed
    income_values: Sequence[str] = DEFAULT_INCOME
    expense_values: Sequence[str] = DEFAULT_EXPENSE


class BankAdapter(ABC):
    """银行侧输入格式 -> Standard Statement v1。"""

    def __init__(self, profile: BankProfile):
        self.profile = profile

    def column_map(self, raw_columns: Iterable[str]) -> Dict[str, str]:
        aliases = {key: list(values) for key, values in COMMON_ALIASES.items()}
        for key, values in self.profile.aliases.items():
            if key not in COMMON_ALIASES:
                raise ValueError(f"未知的标准映射字段: {key}")
            aliases[key] = list(values) + aliases[key]
        lookup = {}
        for standard, names in aliases.items():
            for alias in [standard] + names:
                normalized = _normalize_header(alias)
                if normalized in lookup and lookup[normalized] != standard:
                    raise ValueError(f"字段别名存在歧义: {alias}")
                lookup[normalized] = standard

        mapping = {}
        for column in raw_columns:
            target = lookup.get(_normalize_header(column))
            if target:
                mapping[column] = target
        targets = list(mapping.values())
        if len(targets) != len(set(targets)):
            raise ValueError("多个源字段映射到同一标准字段；请明确列配置: " + str(mapping))
        return mapping

    def normalize(self, raw: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df = raw.rename(columns=self.column_map(raw.columns)).copy()
        # 只有时间戳而没有独立日期的 Excel，按交易时间提取日期。
        if "交易日期" not in df and "交易时间" in df:
            df["交易日期"] = df["交易时间"]
        essentials = ["交易日期", "余额", "交易用途"]
        missing = [c for c in essentials if c not in df]
        if missing:
            raise ValueError(f"{self.profile.code} 缺少字段: {missing}")
        mode = self.profile.amount_mode
        if mode == "auto":
            if "贷方发生额" in df and "借方发生额" in df:
                mode = "split"
            elif "交易金额" in df and "收支方向" in df:
                mode = "direction"
            else:
                raise ValueError("无法确定收支方式，需要收入+支出双列，或交易金额+收支方向；也可用明确的 signed 模板")
        if mode == "split":
            if "贷方发生额" not in df or "借方发生额" not in df:
                raise ValueError("split 模式要求同时提供贷方发生额和借方发生额")
        elif mode in ("direction", "signed"):
            if "交易金额" not in df:
                raise ValueError(f"{mode} 模式缺少交易金额")
            amount = pd.to_numeric(df["交易金额"].astype(str).str.replace(",", "", regex=False)
                                   .str.replace("¥", "", regex=False).str.replace("￥", "", regex=False), errors="coerce")
            if mode == "direction":
                if "收支方向" not in df:
                    raise ValueError("direction 模式缺少收支方向")
                directions = df["收支方向"].fillna("").astype(str).str.strip().str.upper()
                income = {x.upper() for x in self.profile.income_values}
                expense = {x.upper() for x in self.profile.expense_values}
                unknown = ~directions.isin(income | expense)
                if unknown.any():
                    samples = sorted(directions[unknown].unique().tolist())[:5]
                    raise ValueError(f"无法判断收支方向（{samples}），请检查银行模板中的取值映射")
                df["贷方发生额"] = amount.where(directions.isin(income), 0)
                df["借方发生额"] = amount.where(directions.isin(expense), 0)
            else:
                df["贷方发生额"] = amount.clip(lower=0)
                df["借方发生额"] = (-amount).clip(lower=0)
        else:
            raise ValueError(f"不支持的 amount_mode: {mode}")

        info = metadata.get("account_info", {})
        defaults = {
            "银行编码": self.profile.code,
            "银行名称": self.profile.name,
            "账号": info.get("账号", ""),
            "账户户名": info.get("户名", ""),
            "开户行": info.get("开户行", ""),
            "币种": "CNY",
            "交易时间": "",
            "交易流水号": "",
            "对方账号": "",
            "对方户名": "",
            "对方开户行": "",
            "交易用途": "",
        }
        for col, default in defaults.items():
            if col not in df:
                df[col] = default
        for col in ("账号", "账户户名", "开户行"):
            df[col] = df[col].fillna("").replace("", defaults[col])
        # 未识别银行时，同账号也可能属于不同银行。按来源 Sheet 隔离，避免误合并。
        if self.profile.code == "GENERIC":
            df["银行编码"] = f"GENERIC_SOURCE_{metadata['source_index']}_SHEET_{metadata['sheet_index']}"
        # 不能把未知账号的不同工作表混成一个账户
        if df["账号"].fillna("").astype(str).str.strip().eq("").any():
            unknown_id = f"UNSPECIFIED:{metadata['source_index']}:{metadata['sheet_index']}"
            df["账号"] = df["账号"].fillna("").replace("", unknown_id)
        df["source_file"] = metadata["source_file"]
        df["source_sheet"] = metadata["source_sheet"]
        df["source_row"] = np.arange(len(df), dtype="int64") + metadata["header_row"] + 2
        df["source_page"] = int(metadata.get("source_page", 0))
        df["extraction_method"] = metadata.get("extraction_method", "excel_mapping")
        df["recognition_score"] = float(metadata.get("recognition_score", 1.0))
        df["review_required"] = bool(metadata.get("review_required", False))
        result = df.loc[:, list(STANDARD_COLUMNS)].copy()
        validate_standard_frame(result)
        return result


class ABCAdapter(BankAdapter):
    def __init__(self):
        super().__init__(BankProfile("ABC", "中国农业银行", ("中国农业银行", "农业银行", "农行", "abc")))


class ICBCAdapter(BankAdapter):
    def __init__(self):
        super().__init__(BankProfile("ICBC", "中国工商银行", ("中国工商银行", "工商银行", "工行", "icbc")))


class CCBAdapter(BankAdapter):
    def __init__(self):
        super().__init__(BankProfile("CCB", "中国建设银行", ("中国建设银行", "建设银行", "建行", "ccb")))


class GenericAdapter(BankAdapter):
    def __init__(self):
        super().__init__(BankProfile("GENERIC", "未识别银行"))


def load_profile(path: Union[str, Path]) -> BankAdapter:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    code = obj["code"]
    name = obj.get("name", code)
    return BankAdapter(BankProfile(
        code=code, name=name, recognition=obj.get("recognition", []),
        aliases=obj.get("aliases", {}),
        amount_mode=obj.get("amount_mode", "auto"),
        income_values=obj.get("income_values", list(DEFAULT_INCOME)),
        expense_values=obj.get("expense_values", list(DEFAULT_EXPENSE)),
    ))


class AdapterRegistry:
    def __init__(self, custom_profile=None):
        builtin = [ABCAdapter(), ICBCAdapter(), CCBAdapter(), GenericAdapter()]
        self.adapters = {x.profile.code: x for x in builtin}
        if custom_profile:
            adapter = load_profile(custom_profile)
            if adapter.profile.code in self.adapters:
                raise ValueError("自定义银行编码不能覆盖内置银行编码")
            self.adapters[adapter.profile.code] = adapter

    def resolve(self, hint: str, requested: str = "auto") -> BankAdapter:
        if requested.lower() != "auto":
            requested = requested.upper()
            if requested not in self.adapters:
                raise ValueError("不存在银行模板: " + requested + "，可选：" + ",".join(self.adapters))
            return self.adapters[requested]
        name = hint.lower()
        for code, adapter in self.adapters.items():
            if code == "GENERIC":
                continue
            if any(marker.lower() in name for marker in adapter.profile.recognition):
                return adapter
        return self.adapters["GENERIC"]


def _account_info(header_area: pd.DataFrame) -> dict:
    lines = []
    for row in header_area.fillna("").itertuples(index=False, name=None):
        lines.append(" ".join(str(v) for v in row if str(v).strip()))
    text = "\n".join(lines)
    info = {}
    for field_name, labels in (
        ("账号", ("账号", "银行账号", "账户号码", "账户")),
        ("户名", ("户名", "账户名称", "单位名称")),
        ("开户行", ("开户行", "开户机构")),
    ):
        for label in labels:
            match = re.search(r"(?:^|\s)" + re.escape(label) + r"\s*[：:]\s*([^\s]+)", text)
            if match:
                info[field_name] = match.group(1)
                break
    return info


def _find_header(probe: pd.DataFrame, adapters=None) -> Optional[int]:
    date_headers = {_normalize_header(x) for x in COMMON_ALIASES["交易日期"] + COMMON_ALIASES["交易时间"]}
    money_headers = {_normalize_header(x) for key in ("贷方发生额", "借方发生额", "交易金额")
                     for x in COMMON_ALIASES[key]}
    date_headers.update(["交易日期", "交易时间"])
    money_headers.update(["贷方发生额", "借方发生额", "交易金额"])
    for adapter in adapters or []:
        for key in ("交易日期", "交易时间"):
            date_headers.update(_normalize_header(v) for v in adapter.profile.aliases.get(key, []))
        for key in ("贷方发生额", "借方发生额", "交易金额"):
            money_headers.update(_normalize_header(v) for v in adapter.profile.aliases.get(key, []))
    for idx, row in probe.iterrows():
        columns = {_normalize_header(cell) for cell in row if pd.notna(cell)}
        if columns & date_headers and columns & money_headers:
            return int(idx)
    return None


class StatementImporter:
    """读取一份或多份 Excel，各表独立适配，合并为相同列集合。"""

    def __init__(self, bank="auto", profile_path=None):
        self.bank = bank
        self.registry = AdapterRegistry(profile_path)

    def read(self, files):
        paths = [files] if isinstance(files, (str, Path)) else list(files)
        if not paths:
            raise ValueError("至少提供一个流水文件")
        frames, sources = [], []
        for source_index, name in enumerate(paths, start=1):
            path = Path(name)
            if path.suffix.lower() not in (".xls", ".xlsx"):
                raise ValueError(f"不支持的文件后缀: {path.name}")
            with pd.ExcelFile(path) as workbook:
                for sheet_index, sheet in enumerate(workbook.sheet_names, start=1):
                    preview = pd.read_excel(workbook, sheet_name=sheet, header=None, nrows=50, dtype=str)
                    header_row = _find_header(preview, self.registry.adapters.values())
                    if header_row is None:
                        continue
                    pre = preview.iloc[:header_row]
                    # 识别仅使用前导银行名称与文件名，避免交易对手包含其它银行时误判。
                    hint = path.stem + " " + " ".join(pre.fillna("").astype(str).values.ravel().tolist())
                    adapter = self.registry.resolve(hint, self.bank)
                    raw = pd.read_excel(workbook, sheet_name=sheet, header=header_row, dtype=str)
                    if raw.empty:
                        continue
                    meta = {
                        "source_file": path.name,
                        "source_sheet": sheet,
                        "header_row": header_row,
                        "source_index": source_index,
                        "sheet_index": sheet_index,
                        "account_info": _account_info(pre),
                    }
                    canonical = adapter.normalize(raw, meta)
                    frames.append(canonical)
                    sources.append({
                        "file": path.name,
                        "sheet": sheet,
                        "adapter": adapter.profile.code,
                        "header_row": header_row + 1,
                        "original_columns": [str(c) for c in raw.columns],
                        "normalized_columns": adapter.column_map(raw.columns),
                        "account_info": meta["account_info"],
                    })
        if not frames:
            raise ValueError("未找到可解析的流水工作表：请检查 Excel 及字段映射模板")
        result = pd.concat(frames, ignore_index=True)
        validate_standard_frame(result)
        return result, sources
