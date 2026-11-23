"""Task 2: 报告登记（哈希/去重/损坏/加密/超限/无文字层）测试。

用最小合法 PDF 字节与异常字节验证，不依赖真实报告。
"""
import hashlib
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.reports import (
    register_report,
    ReportMetadata,
    ReportError,
    MAX_REPORT_BYTES,
)
from pypdf import PdfWriter
import io


def make_min_pdf():
    """生成一个只有一页、无文字层的最小 PDF。"""
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    return buf.getvalue()


def meta(**kw):
    d = dict(company_id="byd", company_name="比亚迪", report_year=2024,
             kind="sustainability")
    d.update(kw)
    return ReportMetadata(**d)


class TestRegisterReport:
    def test_hashes_bytes_and_returns_report(self, tmp_path):
        pdf = make_min_pdf()
        rep = register_report(pdf, meta(), storage_dir=tmp_path)
        assert rep.sha256 == hashlib.sha256(pdf).hexdigest()
        assert rep.media_type == "application/pdf"
        assert rep.status in ("registered", "parsed", "partial")
        assert rep.page_count >= 1

    def test_duplicate_same_hash_rejected(self, tmp_path):
        pdf = make_min_pdf()
        register_report(pdf, meta(), storage_dir=tmp_path)
        with pytest.raises(ReportError) as e:
            register_report(pdf, meta(), storage_dir=tmp_path)
        assert e.value.code == "DUPLICATE_REPORT"

    def test_corrupt_bytes_rejected(self, tmp_path):
        with pytest.raises(ReportError) as e:
            register_report(b"not a pdf at all", meta(), storage_dir=tmp_path)
        assert e.value.code == "CORRUPT_PDF"

    def test_oversize_rejected(self, tmp_path):
        with pytest.raises(ReportError) as e:
            register_report(b"%PDF" + b"0" * (MAX_REPORT_BYTES + 1),
                            meta(), storage_dir=tmp_path)
        assert e.value.code == "FILE_TOO_LARGE"

    def test_no_text_layer_flagged(self, tmp_path):
        """无文字层 → 报告 issues 含 NO_TEXT_LAYER，不静默当成功解析。"""
        pdf = make_min_pdf()
        rep = register_report(pdf, meta(), storage_dir=tmp_path)
        codes = [i.code for i in rep.issues]
        assert "NO_TEXT_LAYER" in codes

    def test_real_path_not_exposed_in_report(self, tmp_path):
        """真实文件路径不得出现在返回给模型的 Report 中。"""
        pdf = make_min_pdf()
        rep = register_report(pdf, meta(), storage_dir=tmp_path)
        dump = str(rep)
        assert str(tmp_path) not in dump
        assert ".pdf" not in dump
