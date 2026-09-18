"""Text similarity used for duplicate detection and story clustering.

Uses scikit-learn TF-IDF + cosine similarity when it is available, and falls
back to token-overlap scoring otherwise, so the app still groups stories on a
machine where scikit-learn cannot be installed.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from utils.logging_setup import get_logger
from utils.textutil import normalize_text, overlap_ratio, token_set

logger = get_logger(__name__)

try:  # optional dependency - the app works without it
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    SKLEARN_AVAILABLE = True
except Exception:  # pragma: no cover - only hit when sklearn is missing
    SKLEARN_AVAILABLE = False
    logger.info("scikit-learn not available - using token-overlap similarity")


class SimilarityIndex:
    """Compare one new document against a fixed set of documents.

    Fitting the vectoriser on the corpus *plus* the query keeps IDF meaningful
    even when the corpus is small.
    """

    def __init__(self, documents: Sequence[str]) -> None:
        self.documents: List[str] = [normalize_text(doc) for doc in documents]
        self._token_sets = [token_set(doc) for doc in self.documents]

    def query(self, text: str) -> List[float]:
        """Similarity of ``text`` against every document, in the same order."""
        if not self.documents:
            return []
        normalized = normalize_text(text)
        if not normalized.strip():
            return [0.0] * len(self.documents)
        if SKLEARN_AVAILABLE:
            scores = self._tfidf_scores(normalized)
            if scores is not None:
                return scores
        return self._token_scores(normalized)

    def _tfidf_scores(self, normalized: str) -> Optional[List[float]]:
        try:
            corpus = self.documents + [normalized]
            vectorizer = TfidfVectorizer(
                ngram_range=(1, 2), min_df=1, sublinear_tf=True, stop_words="english",
            )
            matrix = vectorizer.fit_transform(corpus)
            similarities = cosine_similarity(matrix[-1], matrix[:-1])[0]
            return [float(value) for value in similarities]
        except Exception as exc:  # empty vocabulary, all-stopword docs, ...
            logger.debug("TF-IDF similarity failed (%s) - falling back to tokens", exc)
            return None

    def _token_scores(self, normalized: str) -> List[float]:
        query_tokens = token_set(normalized)
        return [overlap_ratio(query_tokens, tokens) for tokens in self._token_sets]


def text_similarity(left: str, right: str) -> float:
    """Similarity of two individual strings (0-1)."""
    if not left or not right:
        return 0.0
    index = SimilarityIndex([left])
    scores = index.query(right)
    return scores[0] if scores else 0.0


def title_similarity(left: str, right: str) -> float:
    """Token-overlap similarity of two headlines (robust for short text)."""
    return overlap_ratio(token_set(left), token_set(right))


def set_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """Overlap of two entity lists (fighters, events...)."""
    left_set = {normalize_text(value) for value in left if value}
    right_set = {normalize_text(value) for value in right if value}
    return overlap_ratio(left_set, right_set)
