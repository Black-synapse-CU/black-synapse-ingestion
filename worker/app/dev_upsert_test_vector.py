"""
Dev helper to upsert a test vector and run a similarity search.
This file is placed under `worker/app/` so it's included in the Docker image.
"""
import os
import sys
import json
import numpy as np
from qdrant_client import QdrantClient

from app.utils import validate_vector


def main():
    qdrant_url = os.getenv('QDRANT_URL', 'http://qdrant:6333')
    collection = os.getenv('QDRANT_COLLECTION', 'black_synapse_documents')
    dim = int(os.getenv('EMBEDDING_DIM', '1536'))

    print(f"Using Qdrant URL: {qdrant_url}, collection: {collection}, dim: {dim}")

    qc = QdrantClient(url=qdrant_url)

    # Create a random vector for testing
    vec = np.random.rand(dim).astype(float)

    # validate
    try:
        validate_vector(vec, dim)
    except Exception as e:
        print(f"Vector validation failed: {e}")
        sys.exit(2)

    import uuid

    # Upsert a single point (use UUID for point id)
    point = {
        'id': str(uuid.uuid4()),
        'vector': vec.tolist(),
        'payload': {'source': 'dev', 'note': 'test point'}
    }

    try:
        qc.upsert(collection_name=collection, points=[point])
        print(f"Upserted point {point['id']} into {collection}")
    except Exception as e:
        print(f"Failed to upsert point: {e}")
        sys.exit(3)

    # Search by the same vector
    # qdrant_client version compatibility: if `search` is unavailable, fall back to HTTP API
    try:
        if hasattr(qc, 'search'):
            hits = qc.search(collection_name=collection, query_vector=vec.tolist(), limit=5)
            results = [{'id': h.id, 'score': getattr(h, 'score', None), 'payload': h.payload} for h in hits]
        else:
            import urllib.request
            url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/search"
            body = {"vector": vec.tolist(), "limit": 5, "with_payload": True}
            req_data = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                data = json.load(resp)
            results = []
            for hit in data.get('result', []):
                results.append({'id': hit.get('id'), 'score': hit.get('score'), 'payload': hit.get('payload')})

        print("Search results:")
        print(json.dumps(results, indent=2))
    except Exception as e:
        print(f"Search failed: {e}")
        sys.exit(4)


if __name__ == '__main__':
    main()
