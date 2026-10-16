from pathlib import Path
import pickle
import json

from scipy.sparse import save_npz

from app.core.config import KNOWLEDGE_BASE_DIR
from app.core.config import EMBEDDING_BATCH_SIZE
from app.core.config import EMBEDDING_BACKEND
from app.core.config import KB_BACKEND_FILE
from app.core.config import KB_CHUNKS_FILE
from app.core.config import VECTOR_DB_MATRIX
from app.core.config import VECTOR_DB_VECTORIZER
from app.rag.chunker import chunk_word_blocks
from app.rag.embeddings import generate_sentence_embeddings, generate_tfidf_embeddings
from app.rag.word_loader import load_word_blocks
from app.rag.vector_store import reset_collection, store_chunks


def build_knowledge_base(docx_path: str = str(KNOWLEDGE_BASE_DIR / "knowledge_base_harrison.docx")):
	blocks = load_word_blocks(docx_path)
	chunks = chunk_word_blocks(blocks, source=Path(docx_path).name)
	if not chunks:
		raise ValueError("Word document contains no extractable text.")
	texts = [chunk["text"] for chunk in chunks]
	backend = EMBEDDING_BACKEND
	if backend == "tfidf":
		vectorizer, matrix = generate_tfidf_embeddings(texts)
		with VECTOR_DB_VECTORIZER.open("wb") as file:
			pickle.dump(vectorizer, file)
		save_npz(VECTOR_DB_MATRIX, matrix)
		KB_CHUNKS_FILE.write_text(json.dumps(chunks), encoding="utf-8")
		print("Using sparse TF-IDF retrieval; no model download is required.")
	else:
		try:
			embeddings = generate_sentence_embeddings(texts, EMBEDDING_BATCH_SIZE)
			backend = "sentence_transformer"
		except Exception as error:
			if backend != "auto":
				raise RuntimeError(
					"Sentence Transformer could not be loaded. Set EMBEDDING_BACKEND=tfidf "
					"for offline indexing or configure Hugging Face access."
				) from error
			backend = "tfidf"
			vectorizer, matrix = generate_tfidf_embeddings(texts)
			with VECTOR_DB_VECTORIZER.open("wb") as file:
				pickle.dump(vectorizer, file)
			save_npz(VECTOR_DB_MATRIX, matrix)
			KB_CHUNKS_FILE.write_text(json.dumps(chunks), encoding="utf-8")
			print("Sentence Transformer unavailable; using sparse TF-IDF retrieval.")
	reset_collection()
	if backend == "sentence_transformer":
		store_chunks(chunks, embeddings)
	KB_BACKEND_FILE.write_text(backend, encoding="utf-8")
	print(f"Indexed Word blocks: {len(blocks)}")
	return len(chunks)


if __name__ == "__main__":
	print(f"Knowledge base created with {build_knowledge_base()} chunks.")
