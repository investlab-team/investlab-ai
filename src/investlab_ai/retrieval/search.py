import re

import numpy as np

from investlab_ai.errors import ServiceError
from investlab_ai.providers.openai_provider import validate_vectors
from investlab_ai.schemas import Evidence


def retrieve(
    query: str, query_vector: list[float], rows: list[tuple[Evidence, list[float], int]],
    limit: int,
) -> list[Evidence]:
    if not rows:
        return []
    validate_vectors([query_vector], 1)
    validate_vectors([row[1] for row in rows], len(rows))
    matrix = np.asarray([row[1] for row in rows], dtype=float)
    vector = np.asarray(query_vector, dtype=float)
    if matrix.shape[1] != vector.shape[0]:
        raise ServiceError("EMBEDDING_MODEL_MISMATCH", 409)
    similarities = (matrix @ vector) / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))
    terms = set(re.findall(r"[가-힣a-z0-9]+", query.casefold()))
    lexical = [sum(term in (row[0].section + " " + row[0].text).casefold() for term in terms)
               for row in rows]
    semantic_rank = np.argsort(-similarities, kind="stable")
    lexical_rank = sorted(range(len(rows)), key=lambda index: (-lexical[index], index))
    scores = np.zeros(len(rows))
    for rank, index in enumerate(semantic_rank):
        scores[index] += 1 / (60 + rank + 1)
    for rank, index in enumerate(lexical_rank):
        if lexical[index]:
            scores[index] += 1 / (60 + rank + 1)
    selected = np.argsort(-scores, kind="stable")[:limit]
    return [rows[int(index)][0] for index in selected]
