"""脱敏的合成 PDF/图片样本与模拟视觉模型测试，不调用外部 API。"""
from pathlib import Path
from io import BytesIO
import fitz
from PIL import Image
import pytest
from openpyxl import load_workbook

from main import analyze
from data_processor import DataProcessor
from review_workflow import read_approved_workbook
from vision_llm import LLMSettings, validate_extraction


COLS = ["交易日期", "账号", "贷方发生额", "借方发生额", "余额", "交易用途", "对方账号"]
ROWS = [
    ["2026-03-01", "00123", "90000", "0", "90000", "货款", "OPPOSITE"],
    ["2026-03-02", "00123", "0", "20000", "70000", "采购", "OPPOSITE"],
]


class FakeVisionLLM:
    def __init__(self):
        self.image_calls = 0
        self.text_calls = 0

    def extract_image(self, image_bytes, media_type="image/png"):
        self.image_calls += 1
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        return {"columns": COLS, "rows": ROWS, "bank_hint": "中国农业银行"}

    def extract_text(self, text):
        self.text_calls += 1
        assert len(text) >= 100
        return {"columns": COLS, "rows": ROWS, "bank_hint": "中国农业银行"}


def test_image_roundtrip_mandatory_review(tmp_path):
    path = tmp_path / "statement.png"
    Image.new("RGB", (640, 300), "white").save(path)
    model = FakeVisionLLM()
    target = tmp_path / "result"
    preview = Path(analyze(path, "测试企业", target, llm_client=model))
    assert preview.exists() and preview.name == "recognition_review.xlsx"
    assert model.image_calls == 1
    assert not (target / "analysis.docx").exists()

    book = load_workbook(preview)
    tab = book["标准流水复核"]
    assert tab.max_row == 3
    header = [c.value for c in tab[1]]
    assert "source_page" in header and "extraction_method" in header
    assert tab.cell(2, header.index("账号") + 1).value == "00123"
    assert tab.cell(2, header.index("source_page") + 1).value == 1
    for row in (2, 3):
        tab.cell(row, header.index("审核状态") + 1, "已确认")
    book.save(preview)
    reviewed = read_approved_workbook(preview)
    assert len(reviewed) == 2
    result = Path(analyze(None, "测试企业", target, reviewed_xlsx=preview))
    assert result.exists()
    text = (target / "analysis.md").read_text(encoding="utf-8")
    assert "user_attested_approved" in text
    assert "未经人工复核" not in text


def test_image_cannot_be_auto_analyzed_without_model(tmp_path):
    path = tmp_path / "scan.jpg"
    Image.new("RGB", (200, 100), "white").save(path)
    with pytest.raises(ValueError, match="本地视觉 LLM"):
        DataProcessor().process_all(path)


def test_unreviewed_draft_is_explicit(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "white").save(path)
    result = Path(analyze(
        path, "测试企业", tmp_path / "out",
        llm_client=FakeVisionLLM(), allow_unreviewed=True,
    ))
    assert result.exists()
    assert "未经人工复核" in (tmp_path / "out" / "analysis.md").read_text(encoding="utf-8")


def test_scanned_pdf_vision_flow(tmp_path):
    image = Image.new("RGB", (600, 400), "white")
    buf = BytesIO()
    image.save(buf, "PNG")
    document = fitz.open()
    page = document.new_page(width=600, height=400)
    page.insert_image(fitz.Rect(0, 0, 600, 400), stream=buf.getvalue())
    path = tmp_path / "scan.pdf"
    document.save(path)
    document.close()
    stub = FakeVisionLLM()
    data, report = DataProcessor(llm_client=stub).process_all(path)
    assert len(data) == 2
    assert set(data["source_page"]) == {1}
    assert set(data["extraction_method"]) == {"llm_pdf_vision"}
    assert stub.image_calls == 1
    assert report["review_required_records"] == 2


def test_text_pdf_llm_flow(tmp_path):
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(30, 30, 500, 350),
        ("Bank account statement records transaction 2026-03-01 "
         "credit 90000 debit 0 balance 90000. ") * 4,
        fontsize=11,
    )
    path = tmp_path / "text.pdf"
    document.save(path)
    document.close()
    stub = FakeVisionLLM()
    data, report = DataProcessor(llm_client=stub).process_all(path)
    assert len(data) == 2
    assert set(data["extraction_method"]) == {"llm_pdf_text"}
    assert stub.text_calls == 1


def test_digital_pdf_table_without_llm(tmp_path):
    # 在 PDF 中绘制真实文字表格及线框，不依赖识别模型。
    cells = [
        ["date", "account", "credit", "debit", "balance", "purpose"],
        ["2026-03-01", "00123", "100", "0", "100", "payment"],
        ["2026-03-02", "00123", "0", "30", "70", "supply"],
    ]
    widths = [105, 75, 70, 70, 80, 100]
    xpos = [30]
    for width in widths:
        xpos.append(xpos[-1] + width)
    ypos = [60, 89, 118, 147]
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    for x in xpos:
        page.draw_line((x, ypos[0]), (x, ypos[-1]))
    for y in ypos:
        page.draw_line((xpos[0], y), (xpos[-1], y))
    for row_idx, row in enumerate(cells):
        for col_idx, value in enumerate(row):
            page.insert_text((xpos[col_idx] + 4, ypos[row_idx] + 18), value, fontsize=8)
    path = tmp_path / "typed.pdf"
    document.save(path)
    document.close()
    data, info = DataProcessor().process_all(path)
    assert len(data) == 2
    assert set(data["extraction_method"]) == {"pdf_text_table"}
    assert data["贷方发生额"].tolist() == [100, 0]
    assert info["balance_error_count"] == 0


def test_bad_vision_output_and_remote_protection():
    with pytest.raises(ValueError, match="远端"):
        LLMSettings(url="https://provider.example/v1/chat/completions", model="vision")
    with pytest.raises(ValueError, match="HTTPS"):
        LLMSettings(url="http://provider.example/v1/chat/completions",
                    model="vision", allow_remote=True)
    local = LLMSettings(url="http://127.0.0.1:11434/v1/chat/completions", model="local")
    assert local.model == "local"
    with pytest.raises(ValueError, match="列数"):
        validate_extraction({"columns": ["日期", "金额"], "rows": [["2026-01-01"]]})


def test_pdf_page_limit(tmp_path):
    pdf = fitz.open()
    pdf.new_page()
    pdf.new_page()
    path = tmp_path / "twopages.pdf"
    pdf.save(path)
    pdf.close()
    with pytest.raises(ValueError, match="超过最大 1 页"):
        DataProcessor(max_pages=1).process_all(path)
