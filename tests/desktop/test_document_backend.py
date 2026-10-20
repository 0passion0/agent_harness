import asyncio

import openpyxl

from vertical_harness.storage.backends import DocumentFilesystemBackend


def test_binary_workbook_is_extracted_before_native_text_read_and_paginated(tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["patient", "outcome"])
    for index in range(1, 6):
        sheet.append([f"person{index}", index])
    workbook.save(tmp_path / "source.xlsx")
    backend = DocumentFilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)

    head = asyncio.run(backend.aread("/source.xlsx", offset=0, limit=3))
    later = backend.read("/source.xlsx", offset=5, limit=3)
    assert head.error is None
    assert "patient" in head.file_data["content"]
    assert "person5" not in head.file_data["content"]
    assert "person5" in later.file_data["content"]
    assert backend.read("/missing.xlsx").error
