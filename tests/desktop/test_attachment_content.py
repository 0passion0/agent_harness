import base64

from vertical_harness.desktop_attachments import content_blocks, extract_text


def test_table_attachments_request_local_summary_without_loading_rows(tmp_path):
    path = tmp_path / "large.csv"
    path.write_text("PRIVATE_RAW_ROWS" * 100_000, encoding="utf-8")
    blocks = content_blocks([({"name": path.name, "media_type": "text/csv"}, path)], "Analyze", tmp_path)
    assert "local table summary via run_python" in blocks[1]["text"]
    assert "PRIVATE_RAW_ROWS" not in blocks[1]["text"]
    assert len(blocks[1]["text"]) < 500


def test_extracts_csv_xlsx_docx_and_pdf(tmp_path):
    import pandas as pd
    from docx import Document
    from pypdf import PdfWriter

    csv = tmp_path / "data.csv"
    csv.write_text("value\n12\n", encoding="utf-8")
    assert "12" in extract_text(csv)
    xlsx = tmp_path / "data.xlsx"
    pd.DataFrame({"value": [12]}).to_excel(xlsx, index=False)
    assert "12" in extract_text(xlsx)
    docx = tmp_path / "document.docx"
    document = Document()
    document.add_paragraph("research text")
    document.save(docx)
    assert extract_text(docx) == "research text"
    pdf = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as file:
        writer.write(file)
    assert extract_text(pdf) == "无可提取文字"


def test_limits_text_and_preserves_image_content(tmp_path):
    text = tmp_path / "large.txt"
    text.write_text("a" * 30000, encoding="utf-8")
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    refs = [
        ({"name": path.name, "media_type": media}, path)
        for path, media in [(text, "text/plain"), (image, "image/png")]
    ]
    blocks = content_blocks(refs, "Analyze", tmp_path)
    assert "Path: /large.txt\n" in blocks[1]["text"]
    assert len(blocks[1]["text"]) <= 20000
    assert "truncated" in blocks[1]["text"]
    assert base64.b64encode(b"image").decode() in blocks[2]["image_url"]["url"]
    blocks = content_blocks([refs[0], refs[0]], "Analyze", tmp_path)
    assert sum(len(block["text"]) for block in blocks[1:]) == 20000
    assert all("Path:" in block["text"] for block in blocks[1:])
