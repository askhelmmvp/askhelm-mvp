import pickle
from pathlib import Path
from typing import List, Dict, Any, Union
from sklearn.metrics.pairwise import cosine_similarity

from config import KB_INDEX_PATH as _DEFAULT_INDEX


class AskHelmComplianceRetriever:
    def __init__(self, index_path: Union[str, Path, None] = None) -> None:
        if index_path is None:
            self.index_path = _DEFAULT_INDEX
        else:
            p = Path(index_path)
            self.index_path = p if p.is_absolute() else p.resolve()
        with self.index_path.open("rb") as f:
            payload = pickle.load(f)
        self.vectorizer = payload["vectorizer"]
        self.matrix = payload["matrix"]
        self.metadata = payload["metadata"]

    def search(self, query: str, top_k: int = 5, min_score: float = 0.08) -> List[Dict[str, Any]]:
        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).flatten()
        ranked = scores.argsort()[::-1]
        results: List[Dict[str, Any]] = []
        for idx in ranked[: max(top_k * 3, top_k)]:
            score = float(scores[idx])
            if score < min_score:
                continue
            item = dict(self.metadata[idx])
            item["score"] = round(score, 4)
            results.append(item)
            if len(results) >= top_k:
                break
        return results

if __name__ == "__main__":
    r = AskHelmComplianceRetriever()  # uses KB_INDEX_PATH from config
    for q in [
        "does Tier III apply in Norwegian Sea",
        "what is a major non-conformity",
        "how often do liferafts need servicing",
    ]:
        print(f"\nQUERY: {q}")
        for hit in r.search(q):
            print(hit["score"], hit["id"], hit["topic"], "->", hit["source_reference"])
