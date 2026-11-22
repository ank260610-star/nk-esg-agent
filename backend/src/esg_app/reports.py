"""报告登记（Task 2）：字节哈希、受控保存、损坏/加密/超限/重复/无文字层检测。

返回给模型的 Report 不含真实文件路径（安全边界）。
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MAX_REPORT_BYTES = 100 * 1024 * 1024  # 100MB 上限
PARSER_VERSION = "pdfplumber-0.11.11+coord-v1"


@dataclass
class Issue:
    code: str
    message: str
    report_id: Optional[str] = None
    pdf_page: Optional[int] = None
    record_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "report_id": self.report_id, "pdf_page": self.pdf_page,
                "record_id": self.record_id}


class ReportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ReportMetadata:
    company_id: str
    company_name: str
    report_year: int
    kind: str = "sustainability"  # sustainability/annual/other
    published_date: Optional[str] = None
    source_url: Optional[str] = None


@dataclass
class Report:
    report_id: str
    sha256: str
    company_id: str
    company_name: str
    report_year: int
    published_date: Optional[str]
    source_url: Optional[str]
    kind: str
    media_type: str
    page_count: int
    parser_version: Optional[str]
    status: str
    issues: list[Issue] = field(default_factory=list)

    def __str__(self) -> str:
        # 不暴露真实路径；只列字段值
        return (f"Report(report_id={self.report_id}, sha256={self.sha256[:12]}..., "
                f"page_count={self.page_count}, status={self.status})")

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "sha256": self.sha256,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "published_date": self.published_date,
            "source_url": self.source_url,
            "kind": self.kind,
            "media_type": self.media_type,
            "page_count": self.page_count,
            "parser_version": self.parser_version,
            "status": self.status,
            "issues": [i.to_dict() for i in self.issues],
        }


def _looks_encrypted(data: bytes) -> bool:
    return b"/Encrypt" in data[:4096]


def register_report(file_bytes: bytes, metadata: ReportMetadata,
                    storage_dir: Path | str) -> Report:
    """登记报告：校验→哈希→去重→受控保存→探测文字层。"""
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    if len(file_bytes) > MAX_REPORT_BYTES:
        raise ReportError("FILE_TOO_LARGE",
                          f"report exceeds {MAX_REPORT_BYTES} bytes limit")
    if not file_bytes.startswith(b"%PDF"):
        raise ReportError("CORRUPT_PDF", "missing PDF signature")
    if _looks_encrypted(file_bytes):
        raise ReportError("ENCRYPTED_PDF", "encrypted PDF is not supported")

    sha = hashlib.sha256(file_bytes).hexdigest()

    # 去重：同一 sha 已登记过 → 拒绝
    index = storage_dir / "_index.tsv"
    seen: dict[str, str] = {}
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                seen[parts[0]] = parts[1]
    if sha in seen:
        raise ReportError("DUPLICATE_REPORT", "report with same sha256 already registered")

    # 用 pdfplumber 打开探测页数与文字层
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            # 探测前若干页是否有文字层
            probe_n = min(page_count, 10)
            has_text = False
            for i in range(probe_n):
                t = pdf.pages[i].extract_text() or ""
                if len(t.strip()) > 0:
                    has_text = True
                    break
    except ReportError:
        raise
    except Exception as exc:
        raise ReportError("CORRUPT_PDF", f"cannot open PDF: {type(exc).__name__}")

    report_id = f"rpt_{sha[:16]}"

    # 受控保存（真实路径只落盘，不进 Report）
    safe_path = storage_dir / f"{report_id}.pdf"
    safe_path.write_bytes(file_bytes)

    # 更新索引
    with index.open("a", encoding="utf-8") as f:
        f.write(f"{sha}\t{report_id}\n")

    issues: list[Issue] = []
    if not has_text:
        issues.append(Issue(
            code="NO_TEXT_LAYER",
            message="no extractable text layer detected; OCR not enabled in this version",
            report_id=report_id,
        ))

    return Report(
        report_id=report_id,
        sha256=sha,
        company_id=metadata.company_id,
        company_name=metadata.company_name,
        report_year=metadata.report_year,
        published_date=metadata.published_date,
        source_url=metadata.source_url,
        kind=metadata.kind,
        media_type="application/pdf",
        page_count=page_count,
        parser_version=PARSER_VERSION,
        status="registered",
        issues=issues,
    )
