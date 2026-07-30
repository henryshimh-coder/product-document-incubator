from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from docx import Document


def extractor_module():
    """Loads the real extraction adapter for an observable missing-feature RED."""
    return importlib.import_module("src.infrastructure.files.extractor")


def _write_pdf(path: Path) -> None:
    stream = b"BT /F1 18 Tf 72 720 Td (PDF extracted content) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, object_body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode())
        payload.extend(object_body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    payload.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    )
    payload.extend(trailer.encode())
    path.write_bytes(payload)


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    _write_pdf(tmp_path / "sample.pdf")
    document = Document()
    document.add_heading("DOCX 标题", level=1)
    document.add_paragraph("DOCX 提取内容")
    document.save(tmp_path / "sample.docx")
    (tmp_path / "sample.txt").write_text("TXT 提取内容", encoding="utf-8")
    (tmp_path / "sample.md").write_text("# 一级标题\n\nMD 提取内容", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("fixture_name", ["sample.pdf", "sample.docx", "sample.txt", "sample.md"])
def test_extract_supported_document(fixture_dir: Path, fixture_name: str) -> None:
    """Catches an allowed archive format reaching Ingest without extractable cited text."""
    result = extractor_module().extract_document(fixture_dir / fixture_name, source_id="SRC-001")

    assert result.text.strip()
    assert result.chunks
    assert all(chunk.locator for chunk in result.chunks)


def test_document_chunks_cap_length_and_overlap(tmp_path: Path) -> None:
    """Catches out-of-bound chunks or a missing overlap losing adjacent source context."""
    source_text = "a" * 2200
    path = tmp_path / "long.txt"
    path.write_text(source_text, encoding="utf-8")

    result = extractor_module().extract_document(path, source_id="SRC-001")

    assert [(chunk.char_start, chunk.char_end) for chunk in result.chunks] == [
        (0, 2000),
        (1850, 2200),
    ]
    assert [chunk.text for chunk in result.chunks] == [
        source_text[:2000],
        source_text[1850:],
    ]


def test_docx_locator_retains_heading_and_paragraph_number(fixture_dir: Path) -> None:
    """Catches DOCX citations losing the title context needed to locate a paragraph."""
    result = extractor_module().extract_document(fixture_dir / "sample.docx", source_id="SRC-001")

    assert any("title:DOCX 标题" in chunk.locator for chunk in result.chunks)
    assert any("paragraph:" in chunk.locator for chunk in result.chunks)
