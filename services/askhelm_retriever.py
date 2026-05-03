import logging
import pickle
import re
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Matches "chapter 10", "regulation 14", "annex i", "annex 1", "annex vi", etc.
_CHAPTER_PATTERN = re.compile(
    r"\b(chapter|regulation|reg|rule|annex|article|section)\s+([\divxlcIVXLC]+)\b",
    re.IGNORECASE,
)


def _extract_chapter_refs(query: str) -> list:
    """Return list of normalised chapter/regulation references found in the query."""
    refs = []
    for m in _CHAPTER_PATTERN.finditer(query):
        refs.append(f"{m.group(1).lower()} {m.group(2).lower()}")
    return refs


class AskHelmComplianceRetriever:
    def __init__(self, index_path: Union[str, Path, None] = None) -> None:
        if index_path is None:
            from storage_paths import get_compliance_index_path
            resolved = get_compliance_index_path()
        else:
            p = Path(index_path)
            resolved = p if p.is_absolute() else p.resolve()

        self.index_path = resolved

        if not self.index_path.exists():
            # Attempt to seed and build the index before failing.
            logger.warning(
                "compliance_retriever: index not found at %s — triggering seed", self.index_path
            )
            from services.compliance_ingest import seed_if_empty, rebuild_index
            seed_if_empty()
            if not self.index_path.exists():
                # Seed wrote chunks but index build may have failed; try explicit rebuild.
                rebuild_index()

        with self.index_path.open("rb") as f:
            payload = pickle.load(f)
        self.vectorizer = payload["vectorizer"]
        self.matrix = payload["matrix"]
        self.metadata = payload["metadata"]
        logger.info(
            "compliance_retriever: loaded index — chunks=%d path=%s",
            len(self.metadata), self.index_path,
        )

    def search(
        self, query: str, top_k: int = 5, min_score: float = 0.05
    ) -> List[Dict[str, Any]]:
        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).flatten()
        ranked = scores.argsort()[::-1]

        results: List[Dict[str, Any]] = []
        for idx in ranked[: max(top_k * 4, 20)]:
            score = float(scores[idx])
            if score < min_score:
                break
            item = dict(self.metadata[idx])
            item["score"] = round(score, 4)
            results.append(item)
            if len(results) >= top_k:
                break

        # Chapter/regulation number boost: if the query references a specific
        # chapter or regulation number (e.g. "chapter 9", "annex I") and the
        # top-ranked results don't contain that reference, promote matching chunks.
        chapter_refs = _extract_chapter_refs(query)
        if chapter_refs:
            results = self._boost_chapter_matches(results, chapter_refs, scores, min_score, top_k)


        logger.info(
            "compliance_retriever: query=%r chapter_refs=%s chunks_searched=%d results=%d "
            "top_score=%.4f min_score=%.4f",
            query[:60],
            chapter_refs or [],
            len(self.metadata),
            len(results),
            float(scores[ranked[0]]) if len(ranked) else 0.0,
            min_score,
        )
        if results:
            for r in results[:3]:
                logger.debug(
                    "  hit score=%.4f id=%s source=%r",
                    r["score"], r.get("id", "?"), r.get("source_reference", "")[:60],
                )
        else:
            logger.warning(
                "compliance_retriever: NO RESULTS for query=%r "
                "(top raw score=%.4f, threshold=%.4f) — sources available: %s",
                query[:60],
                float(scores[ranked[0]]) if len(ranked) else 0.0,
                min_score,
                ", ".join(
                    {m.get("source", m.get("document", "?")) for m in self.metadata}
                ),
            )

        return results

    def _boost_chapter_matches(
        self,
        results: List[Dict[str, Any]],
        chapter_refs: list,
        scores,
        min_score: float,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Check whether any existing results contain all queried chapter references.
        If the top result doesn't match, scan all chunks for one that does and
        promote it to position 0 with a boosted score.
        """
        def _contains_refs(chunk: Dict) -> bool:
            haystack = (
                (chunk.get("section") or "") + " " +
                (chunk.get("topic") or "") + " " +
                (chunk.get("content") or "")
            ).lower()
            return all(ref in haystack for ref in chapter_refs)

        # If the top result already matches, nothing to do.
        if results and _contains_refs(results[0]):
            return results

        # Scan all metadata for a better match.
        for idx, chunk in enumerate(self.metadata):
            if _contains_refs(chunk):
                raw_score = float(scores[idx])
                if raw_score < min_score:
                    raw_score = min_score  # ensure it passes threshold
                boosted = dict(chunk)
                boosted["score"] = round(max(raw_score, 0.15), 4)  # floor at threshold
                # Remove duplicate if already in results
                results = [r for r in results if r.get("id") != chunk.get("id")]
                results.insert(0, boosted)
                results = results[:top_k]
                logger.debug(
                    "compliance_retriever: chapter boost applied refs=%s → id=%s score=%.4f",
                    chapter_refs, chunk.get("id"), boosted["score"],
                )
                return results

        return results


    def search_with_yacht(
        self,
        query: str,
        yacht_id: str,
        selected_regulations: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """
        Combined search across global regulations and yacht-specific compliance docs.

        If selected_regulations is provided and non-empty, global results are filtered
        to those sources; falls back to all global results if none survive the filter.
        Yacht-specific chunks are prepended (higher priority for vessel procedures).
        """
        global_results = self.search(query, top_k=top_k * 3, min_score=min_score)

        if selected_regulations:
            filtered = [
                r for r in global_results
                if any(
                    sel.lower() in (r.get("source") or "").lower()
                    for sel in selected_regulations
                )
            ]
            if not filtered:
                filtered = global_results
                logger.debug(
                    "compliance_retriever: no global results within selected_regulations=%s — using all",
                    selected_regulations,
                )
        else:
            filtered = global_results

        yacht_results = self._search_yacht_index(query, yacht_id, top_k, min_score)

        combined = list(yacht_results)
        seen_ids = {r.get("id") or r.get("source_reference", "") for r in combined}
        for r in filtered:
            rid = r.get("id") or r.get("source_reference", "")
            if rid not in seen_ids:
                combined.append(r)
                seen_ids.add(rid)

        result = combined[:top_k]
        logger.info(
            "compliance_retriever: combined_search yacht=%s selected=%s "
            "global_hits=%d yacht_hits=%d final=%d",
            yacht_id,
            selected_regulations or "all",
            len(filtered),
            len(yacht_results),
            len(result),
        )
        return result

    def _search_yacht_index(
        self, query: str, yacht_id: str, top_k: int, min_score: float
    ) -> List[Dict[str, Any]]:
        """Search the yacht-specific compliance TF-IDF index. Returns [] if none exists."""
        from storage_paths import get_yacht_compliance_index_path
        idx_path = get_yacht_compliance_index_path(yacht_id)
        if not idx_path.exists():
            return []
        try:
            with open(idx_path, "rb") as fh:
                payload = pickle.load(fh)
            q = payload["vectorizer"].transform([query])
            scores = cosine_similarity(q, payload["matrix"]).flatten()
            ranked = scores.argsort()[::-1]
            results: List[Dict[str, Any]] = []
            for i in ranked[:max(top_k * 4, 20)]:
                score = float(scores[i])
                if score < min_score:
                    break
                item = dict(payload["metadata"][i])
                item["score"] = round(score, 4)
                item["_from_yacht"] = True
                results.append(item)
                if len(results) >= top_k:
                    break
            logger.debug(
                "compliance_retriever: yacht_index_search yacht=%s hits=%d", yacht_id, len(results)
            )
            return results
        except Exception as exc:
            logger.warning(
                "compliance_retriever: yacht index search failed yacht=%s: %s", yacht_id, exc
            )
            return []


if __name__ == "__main__":
    r = AskHelmComplianceRetriever()
    for q in [
        "what is chapter 10 of the ISM Code",
        "does Tier III apply in Norwegian Sea",
        "what is a major non-conformity",
        "how often do liferafts need servicing",
    ]:
        print(f"\nQUERY: {q}")
        for hit in r.search(q):
            print(f"  {hit['score']:.4f}  {hit.get('id')}  {hit.get('source_reference', '')[:70]}")
