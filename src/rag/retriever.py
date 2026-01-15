import os
import json
from typing import List, Tuple

import faiss
import numpy as np
from typing import Optional


from src.rag.doc_loader import load_pdfs_from_data


class RAGRetriever:
    def __init__(self, data_dir: str = "data", index_path: str = "data/faiss.index", docstore_path: str = "data/docstore.json"):
        self.data_dir = data_dir
        self.index_path = index_path
        self.docstore_path = docstore_path
        self.model = None  # lazy-loaded SentenceTransformer
        self.dimension: Optional[int] = None
        self.index = None
        self.docstore = []
        self._ensure_index()

    def _load_model(self):
        if self.model is None:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            self.dimension = self.model.get_sentence_embedding_dimension()

    def _ensure_index(self):
        if os.path.exists(self.index_path) and os.path.exists(self.docstore_path):
            try:
                self.index = faiss.read_index(self.index_path)
                with open(self.docstore_path, "r", encoding="utf-8") as f:
                    self.docstore = json.load(f)
                return
            except Exception:
                pass

        # build index
        docs = load_pdfs_from_data(self.data_dir)
        texts = [d["text"] for d in docs]
        if not texts:
            # empty index
            # ensure dimension is set by loading model
            self._load_model()
            self.index = faiss.IndexFlatL2(self.dimension)
            self.docstore = []
            return

        # load model lazily for embedding creation
        self._load_model()
        embeddings = self.model.encode(texts, show_progress_bar=True)
        embeddings = np.array(embeddings).astype("float32")
        self.index = faiss.IndexFlatL2(self.dimension)
        self.index.add(embeddings)
        self.docstore = docs
        os.makedirs(os.path.dirname(self.index_path) or ".", exist_ok=True)
        faiss.write_index(self.index, self.index_path)
        with open(self.docstore_path, "w", encoding="utf-8") as f:
            json.dump(self.docstore, f, ensure_ascii=False)

    def get_top_k(self, query: str, k: int = 4) -> List[Tuple[float, str]]:
        if self.index is None or len(self.docstore) == 0:
            return []
        # ensure model loaded to compute query embedding
        self._load_model()
        q_emb = self.model.encode([query]).astype("float32")
        D, I = self.index.search(q_emb, k)
        results = []
        for score, idx in zip(D[0], I[0]):
            if idx < 0 or idx >= len(self.docstore):
                continue
            results.append((float(score), self.docstore[idx]["text"]))
        return results
