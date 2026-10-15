"""多格式银行流水导入：Excel、文字 PDF、扫描 PDF、图片 -> 统一 DataFrame。

文字型 PDF 优先从可选择的表格文本直接抽取；没有可识别表格时，
文本交给 LLM，扫描 PDF 和图片交给视觉 LLM。
所有非 Excel 提取的记录均标记为待人工复核。
"""
from io import BytesIO
from pathlib import Path
import pandas as pd
import fitz
from PIL import Image, ImageOps

from bank_adapters import AdapterRegistry, StatementImporter, _find_header
from statement_schema import validate_standard_frame
from vision_llm import validate_extraction

EXCEL_SUFFIXES = {".xlsx", ".xls"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_SUFFIXES = EXCEL_SUFFIXES | IMAGE_SUFFIXES | {".pdf"}


def image_as_png(image):
    image = ImageOps.exif_transpose(image)
    image.load()
    if image.width * image.height > 30_000_000:
        raise ValueError("图片像素超过 3000 万；请先分割图片")
    image = image.convert("RGB")
    image.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def structural_score(adapter, raw, normalized):
    """衡量字段可用性和数值格式；不是 OCR 正确率或真实性评分。"""
    mapped = set(adapter.column_map(raw.columns).values())
    coverage = sum((
        "交易日期" in mapped or "交易时间" in mapped,
        "余额" in mapped,
        "交易用途" in mapped,
        ("贷方发生额" in mapped and "借方发生额" in mapped)
        or ("交易金额" in mapped and "收支方向" in mapped)
        or (adapter.profile.amount_mode == "signed" and "交易金额" in mapped),
    )) / 4
    dates = pd.to_datetime(normalized["交易日期"], errors="coerce")
    dates_ratio = float(dates.notna().mean())
    amount_columns = normalized[["贷方发生额", "借方发生额", "余额"]].copy()
    for col in amount_columns:
        amount_columns[col] = pd.to_numeric(
            amount_columns[col].astype(str).str.replace(",", "", regex=False)
            .str.replace("￥", "", regex=False).str.replace("¥", "", regex=False),
            errors="coerce",
        )
    money_ratio = float(amount_columns.notna().all(axis=1).mean())
    return round(0.4 * coverage + 0.3 * dates_ratio + 0.3 * money_ratio, 4)


class UniversalStatementImporter:
    def __init__(self, bank="auto", profile_path=None, llm_client=None, max_pages=200):
        self.bank = bank
        self.registry = AdapterRegistry(profile_path)
        self.excel_importer = StatementImporter(bank, profile_path)
        self.llm_client = llm_client
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages 必须是正整数")
        self.max_pages = max_pages

    def read(self, files):
        paths = [Path(files)] if isinstance(files, (str, Path)) else [Path(x) for x in files]
        if not paths:
            raise ValueError("至少提供一个银行流水文件")
        invalid = [str(p) for p in paths if p.suffix.lower() not in SUPPORTED_SUFFIXES]
        if invalid:
            raise ValueError("不支持的格式: " + ", ".join(invalid))
        excels = [p for p in paths if p.suffix.lower() in EXCEL_SUFFIXES]
        others = [p for p in paths if p.suffix.lower() not in EXCEL_SUFFIXES]
        frames, origins = [], []
        if excels:
            excel_data, excel_sources = self.excel_importer.read(excels)
            frames.append(excel_data)
            origins.extend({
                **meta, "source_type": "excel", "extraction_method": "excel_mapping",
                "recognition_score": 1.0, "review_required": False,
            } for meta in excel_sources)
        for source_index, path in enumerate(others, start=len(excels) + 1):
            if path.suffix.lower() == ".pdf":
                data, sources = self._read_pdf(path, source_index)
            else:
                data, sources = self._read_image(path, source_index)
            frames.extend(data)
            origins.extend(sources)
        if not frames:
            raise ValueError("全部文件均无可分析交易数据")
        result = pd.concat(frames, ignore_index=True)
        validate_standard_frame(result)
        return result, origins

    def _adapter(self, hint):
        return self.registry.resolve(hint, self.bank)

    def _convert(self, columns, rows, path, source_index, page, table_no, method, hint, notes=""):
        if not rows:
            return None, None
        adapter = self._adapter(hint)
        raw = pd.DataFrame(rows, columns=columns, dtype=str)
        sheet = f"PAGE_{page}_TABLE_{table_no}"
        meta = {
            "source_file": path.name, "source_sheet": sheet, "header_row": 0,
            "source_index": source_index, "sheet_index": 1, "account_info": {},
            "source_page": page, "extraction_method": method,
            "recognition_score": 0.0, "review_required": True,
        }
        data = adapter.normalize(raw, meta)
        score = structural_score(adapter, raw, data)
        data["recognition_score"] = score
        origin = {
            "file": path.name, "sheet": sheet, "page": page,
            "source_type": "pdf" if path.suffix.lower() == ".pdf" else "image",
            "extraction_method": method, "adapter": adapter.profile.code,
            "recognition_score": score, "review_required": True,
            "original_columns": list(columns),
            "normalized_columns": adapter.column_map(raw.columns),
            "records": len(data), "notes": notes,
        }
        return data, origin

    def _requires_llm(self, path, page):
        if self.llm_client is None:
            raise ValueError(
                f"{path.name} 第 {page} 页无法可靠地直接提取流水；"
                "请配置本地视觉 LLM，或改用可识别的 Excel 文件"
            )

    def _model_result(self, value, path, source_index, page, method, hint):
        value = validate_extraction(value)
        if not value["rows"]:
            return [], [{
                "file": path.name, "page": page,
                "source_type": "pdf" if path.suffix.lower() == ".pdf" else "image",
                "extraction_method": method, "recognition_score": 0.0,
                "review_required": True, "records": 0,
                "notes": value["notes"] or "模型返回无交易记录",
            }]
        data, origin = self._convert(
            value["columns"], value["rows"], path, source_index,
            page, 1, method, hint + " " + value["bank_hint"], value["notes"],
        )
        return [data], [origin]

    def _extract_pdf_tables(self, page, path, source_index, page_no, hint):
        data, origins = [], []
        try:
            tables = page.find_tables()
        except (AttributeError, ValueError, RuntimeError):
            return [], []
        for table_no, table in enumerate(tables.tables, start=1):
            cells = table.extract()
            if not cells:
                continue
            row_idx = _find_header(pd.DataFrame(cells), self.registry.adapters.values())
            if row_idx is None or row_idx >= len(cells) - 1:
                continue
            columns = [str(v).strip() if v is not None else "" for v in cells[row_idx]]
            if any(not item for item in columns) or len(set(columns)) != len(columns):
                continue
            rows = [r for r in cells[row_idx + 1:] if r and any(
                v is not None and str(v).strip() for v in r)]
            if not rows:
                continue
            try:
                frame, origin = self._convert(
                    columns, rows, path, source_index, page_no,
                    table_no, "pdf_text_table", hint,
                )
            except (ValueError, KeyError):
                return [], []
            if frame is None or origin["recognition_score"] < 0.85:
                return [], []
            data.append(frame)
            origins.append(origin)
        return data, origins

    def _read_pdf(self, path, source_index):
        frames, origins = [], []
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError(f"PDF 已加密，需授权解密：{path.name}")
            if len(document) > self.max_pages:
                raise ValueError(f"{path.name} 有 {len(document)} 页，超过最大 {self.max_pages} 页限制")
            for page_index in range(len(document)):
                page = document[page_index]
                page_num = page_index + 1
                text = page.get_text("text")
                hint = path.stem + " " + text[:500]
                found, sources = self._extract_pdf_tables(
                    page, path, source_index, page_num, hint
                )
                if found:
                    frames.extend(found)
                    origins.extend(sources)
                    continue
                # 真正空白页可跳过，记录在识别追踪中。
                if not text.strip() and not page.get_images() and not page.get_drawings():
                    origins.append({
                        "file": path.name, "page": page_num, "source_type": "pdf",
                        "extraction_method": "blank", "records": 0,
                        "review_required": True, "recognition_score": 0.0,
                        "notes": "PDF 空白页",
                    })
                    continue
                self._requires_llm(path, page_num)
                if len(text.strip()) >= 100:
                    value = self.llm_client.extract_text(text)
                    detected, sources = self._model_result(
                        value, path, source_index, page_num, "llm_pdf_text", hint
                    )
                else:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    with Image.open(BytesIO(pixmap.tobytes("png"))) as image:
                        png = image_as_png(image)
                    value = self.llm_client.extract_image(png)
                    detected, sources = self._model_result(
                        value, path, source_index, page_num, "llm_pdf_vision", hint
                    )
                frames.extend(detected)
                origins.extend(sources)
        if not frames:
            raise ValueError(f"未从 {path.name} 识别出可分析交易")
        return frames, origins

    def _read_image(self, path, source_index):
        self._requires_llm(path, 1)
        frames, origins = [], []
        with Image.open(path) as image:
            count = getattr(image, "n_frames", 1)
            if count > self.max_pages:
                raise ValueError(f"图片有 {count} 页，超过最大 {self.max_pages} 页限制")
            for page_idx in range(count):
                image.seek(page_idx)
                png = image_as_png(image)
                value = self.llm_client.extract_image(png)
                detected, sources = self._model_result(
                    value, path, source_index, page_idx + 1,
                    "llm_image_vision", path.stem,
                )
                frames.extend(detected)
                origins.extend(sources)
        if not frames:
            raise ValueError(f"未从图片 {path.name} 识别出交易数据")
        return frames, origins
