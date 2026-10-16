import chromadb

from app.core.config import VECTOR_DB_PATH

client = chromadb.PersistentClient(
    path=str(VECTOR_DB_PATH)
)

collection = client.get_or_create_collection(
    name="medical_knowledge",
    metadata={"hnsw:space": "cosine"},
)


def reset_collection():
    global collection
    client.delete_collection("medical_knowledge")
    collection = client.get_or_create_collection(
        name="medical_knowledge",
        metadata={"hnsw:space": "cosine"},
    )


def store_chunks(chunks, embeddings, batch_size=250):
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        batch_embeddings = embeddings[start:start + batch_size]
        collection.upsert(
            # PDF chunks carry a "page"; Word chunks carry a "block" instead.
            ids=[f"{item['metadata']['source']}-"
                 f"{'p' + str(item['metadata']['page']) if 'page' in item['metadata'] else 'b' + str(item['metadata'].get('block', 0))}"
                 f"-c{start + offset}"
                 for offset, item in enumerate(batch)],
            documents=[item["text"] for item in batch],
            embeddings=batch_embeddings.tolist(),
            metadatas=[item["metadata"] for item in batch],
        )