"""Drawing (sheet) identity: title block first, file name as the fallback."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from drawing_qa.common.grid import read_grid_frame
from drawing_qa.common.titleblock import read_title_block
from drawing_qa.config import Settings
from drawing_qa.model.enums import DocType, ExtractionMethod
from drawing_qa.model.source import Drawing
from drawing_qa.pdf.extract import PageContent


def drawing_for_page(
    file_path: str,
    doc_type: DocType,
    page: PageContent,
    page_count: int,
    settings: Settings | None = None,
) -> Drawing:
    settings = settings or Settings()
    tb = read_title_block(page, settings.title_block)
    stem = PurePosixPath(file_path).stem
    fallback_sheet = str(page.page_index + 1) if page_count > 1 else "1"
    content = "\n".join(sorted(line.text for line in page.lines))
    return Drawing(
        id=f"{file_path}#p{page.page_index + 1}",
        file_path=file_path,
        page_index=page.page_index,
        doc_type=doc_type,
        drawing_no=tb.drawing_no or stem,
        sheet=tb.sheet or fallback_sheet,
        rev=tb.rev,
        title=tb.title,
        status=tb.status,
        descriptor=tb.descriptor,
        title_doc_type=tb.doc_type,
        title_block_method=ExtractionMethod.TEMPLATE if tb.found else ExtractionMethod.FILENAME,
        missing_title_fields=tb.missing,
        grid=read_grid_frame(page, settings.grid),
        page_kind=page.kind,
        width=page.width,
        height=page.height,
        word_count=len(page.words),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
    )
