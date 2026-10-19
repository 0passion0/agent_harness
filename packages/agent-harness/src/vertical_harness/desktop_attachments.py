from __future__ import annotations

import base64
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
from typing import Any


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md', '.json'}:
        return path.read_text(encoding='utf-8')
    if suffix in {'.csv', '.tsv', '.xlsx', '.xlsm'}:
        import pandas as pd
        if suffix in {'.xlsx', '.xlsm'}:
            sheets = pd.read_excel(path, sheet_name=None)
        else:
            sheets = {path.name: pd.read_csv(path, sep='\t' if suffix == '.tsv' else ',')}
        return '\n\n'.join(f'Sheet: {name}\n{frame.to_string(index=False)}' for name, frame in sheets.items())
    if suffix == '.pdf':
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages).strip() or '无可提取文字'
    if suffix == '.docx':
        from docx import Document
        document = Document(path)
        return '\n'.join([paragraph.text for paragraph in document.paragraphs if paragraph.text] +
                         ['\n'.join(' | '.join(cell.text for cell in row.cells) for row in table.rows)
                          for table in document.tables])
    if suffix == '.pptx':
        with ZipFile(path) as presentation:
            slides = sorted(
                (name for name in presentation.namelist()
                 if name.startswith('ppt/slides/slide') and name.endswith('.xml')),
                key=lambda name: int(name.removeprefix('ppt/slides/slide').removesuffix('.xml')),
            )
            return '\n\n'.join(
                f'Slide {index}: ' + ' '.join(
                    element.text for element in ElementTree.fromstring(presentation.read(name))
                    if element.tag.endswith('}t') and element.text
                )
                for index, name in enumerate(slides, 1)
            )
    return 'Opaque attachment; read the referenced file with a supported tool. Its contents have not been extracted.'


def content_blocks(sources: list[tuple[dict[str, str], Path]], text: str, workspace_root: Path) -> str | list[dict[str, Any]]:
    if not sources:
        return text
    blocks: list[dict[str, Any]] = [{'type': 'text', 'text': text}]
    for ref, path in sources:
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
            data = base64.b64encode(path.read_bytes()).decode('ascii')
            blocks.append({'type': 'image_url', 'image_url': {'url': f'data:{ref["media_type"]};base64,{data}'}})
            continue
        path_in_workspace = f'/{path.relative_to(workspace_root).as_posix()}'
        blocks.append({
            'type': 'text',
            'text': (
                f'Attachment: {ref["name"]}\nPath: {path_in_workspace}\n'
                'Start with read_file(offset=0, limit=40); if more is needed, continue with later offsets.'
            ),
        })
    return blocks
