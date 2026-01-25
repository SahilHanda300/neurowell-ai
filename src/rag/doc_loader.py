import os
from typing import List
from PyPDF2 import PdfReader
import shutil

# OCR fallback imports are optional and only used when available.
try:
    from pdf2image import convert_from_path  # type: ignore
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False


def load_pdfs_from_data(data_dir: str = "data") -> List[dict]:
    """Load text chunks from PDFs in `data/`.

    Returns list of {"source": filepath, "text": chunk_text}.
    """
    docs = []
    if not os.path.isdir(data_dir):
        return docs

    for fname in os.listdir(data_dir):
        if not fname.lower().endswith(".pdf"):
            continue
        path = os.path.join(data_dir, fname)
        try:
            reader = PdfReader(path)
            full_text = []
            for p in reader.pages:
                try:
                    txt = p.extract_text() or ""
                except Exception:
                    txt = ""
                full_text.append(txt)
            text = "\n".join(full_text)
            # chunk by roughly `chunk_size` but avoid cutting in the middle of words
            chunk_size = 1000
            i = 0
            L = len(text)
            added_any = False
            while i < L:
                end = min(L, i + chunk_size)
                # If we're not at the end, try to move `end` back to the last whitespace
                # to avoid splitting words. If that would move too far back, try moving
                # forward to the next whitespace within a small window.
                if end < L:
                    last_ws = text.rfind(" ", i, end)
                    if last_ws and last_ws > i + chunk_size // 2:
                        end = last_ws
                    else:
                        # try to find next whitespace up to +200 chars
                        next_ws = text.find(" ", end, min(L, end + 200))
                        if next_ws != -1:
                            end = next_ws
                chunk = text[i:end].strip()
                if chunk:
                    docs.append({"source": fname, "text": chunk})
                    added_any = True
                # advance
                # if we moved to a whitespace boundary, skip the whitespace
                i = end
            if not added_any:
                # Try OCR fallback (best-effort). We prefer to extract
                # text in-memory using pdf2image + pytesseract if available.
                ocr_text = None
                if _OCR_AVAILABLE:
                    try:
                        pages = convert_from_path(path, dpi=300)
                        ocr_pages = []
                        for img in pages:
                            try:
                                ocr_pages.append(pytesseract.image_to_string(img))
                            except Exception:
                                ocr_pages.append("")
                        ocr_text = "\n".join(ocr_pages).strip()
                    except Exception:
                        ocr_text = None

                if ocr_text:
                    # chunk OCR text same as normal flow
                    chunk_size = 1000
                    i = 0
                    L = len(ocr_text)
                    while i < L:
                        end = min(L, i + chunk_size)
                        if end < L:
                            last_ws = ocr_text.rfind(" ", i, end)
                            if last_ws and last_ws > i + chunk_size // 2:
                                end = last_ws
                            else:
                                next_ws = ocr_text.find(" ", end, min(L, end + 200))
                                if next_ws != -1:
                                    end = next_ws
                        chunk = ocr_text[i:end].strip()
                        if chunk:
                            docs.append({"source": fname, "text": chunk})
                            added_any = True
                        i = end

                # If OCR not available or failed, add placeholder so filename
                # matching can still work.
                if not added_any:
                    placeholder = f"[no extractable text in {fname}]"
                    docs.append({"source": fname, "text": placeholder})
        except Exception:
            continue
    return docs
