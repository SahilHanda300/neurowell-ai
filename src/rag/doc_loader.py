import os
from typing import List
from PyPDF2 import PdfReader


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
            # naive chunking by characters
            chunk_size = 1000
            for i in range(0, len(text), chunk_size):
                chunk = text[i : i + chunk_size].strip()
                if chunk:
                    docs.append({"source": fname, "text": chunk})
        except Exception:
            continue
    return docs
