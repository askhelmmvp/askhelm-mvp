from pathlib import Path
from typing import List, Optional
from pypdf import PdfReader
import fitz  # pymupdf

from config import RENDERED_DIR as _RENDERED_DIR


def extract_pdf_text(file_path: str) -> str:
    path = Path(file_path)
    reader = PdfReader(str(path))
    parts = []

    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)

    return "\n\n".join(parts).strip()


def render_pdf_pages_to_images(file_path: str, output_dir: Optional[str] = None) -> List[str]:
    if output_dir is None:
        output_dir = str(_RENDERED_DIR)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    doc = fitz.open(file_path)
    image_paths = []

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        out_path = Path(output_dir) / f"{Path(file_path).stem}_page_{i+1}.png"
        pix.save(str(out_path))
        image_paths.append(str(out_path))

    return image_paths