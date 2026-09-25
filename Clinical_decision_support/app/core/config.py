import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

VECTOR_DB_PATH = PROJECT_ROOT / "chroma_db"
VECTOR_DB_VECTORIZER = VECTOR_DB_PATH / "tfidf_vectorizer.pkl"
VECTOR_DB_MATRIX = VECTOR_DB_PATH / "tfidf_matrix.npz"
KB_BACKEND_FILE = VECTOR_DB_PATH / "embedding_backend.txt"
KB_CHUNKS_FILE = VECTOR_DB_PATH / "tfidf_chunks.json"
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
COFORGE_API_URL = os.getenv("COFORGE_API_URL")
COFORGE_API_KEY = os.getenv("COFORGE_API_KEY")
COFORGE_MODEL = os.getenv("COFORGE_MODEL", "").strip()
COFORGE_CPT_API_URL = os.getenv("COFORGE_CPT_API_URL") or COFORGE_API_URL
COFORGE_CPT_API_KEY = os.getenv("COFORGE_CPT_API_KEY") or COFORGE_API_KEY
COFORGE_CPT_MODEL = os.getenv("COFORGE_CPT_MODEL", "").strip() or COFORGE_MODEL
EMBEDDING_MODEL = os.getenv(
	"EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
).strip()
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "auto").strip().lower()
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
RETRIEVAL_CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "20"))
RETRIEVAL_RESULTS = int(os.getenv("RETRIEVAL_RESULTS", "5"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "12000"))