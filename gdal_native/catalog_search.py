# -*- coding: utf-8 -*-
"""
gdal_native.catalog_search
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Free-text dataset/variable search — ported near-verbatim from
geobridge/semantic/catalog.py, which was already pure stdlib (math, re,
collections.Counter) except for its YAML loading; that's swapped for JSON
against catalog_data/, same as discover.py.

TF-IDF + cosine similarity over every (dataset, subset, variable) triple
in the ARCO snapshot — pure Python, no third-party dependency, corpus is
small (~1000 short strings), rebuilt once and cached.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .discover import _load_arco_snapshot

_OVERRIDES_PATH = Path(__file__).parent.parent / "catalog_data" / "arco_overrides.json"

_TOKEN_RE = re.compile(r"\b\w\w+\b", re.UNICODE)

_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to",
    "from", "by", "with", "as", "is", "are", "be", "been", "being", "was",
    "were", "this", "that", "these", "those", "it", "its", "into", "over",
    "under", "per", "via", "which", "such", "not", "no", "than", "then",
    "there", "here", "also", "can", "may", "will", "would", "should",
})


@dataclass(frozen=True)
class CorpusEntry:
    dataset_id: str
    variable: str
    text: str


def _analyze(text: str) -> list:
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_WORDS]
    grams = list(tokens)
    grams.extend(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
    return grams


class _TfidfModel:
    """Minimal TF-IDF vectoriser: fit a corpus, transform queries, score."""

    def __init__(self, idf: dict, doc_vectors: list) -> None:
        self._idf = idf
        self._doc_vectors = doc_vectors

    @classmethod
    def fit(cls, documents: list) -> "_TfidfModel":
        tokenised = [_analyze(doc) for doc in documents]

        df = Counter()
        for terms in tokenised:
            df.update(set(terms))

        n_docs = len(documents)
        idf = {
            term: math.log((1 + n_docs) / (1 + doc_freq)) + 1.0
            for term, doc_freq in df.items()
        }

        doc_vectors = [cls._vectorize(terms, idf) for terms in tokenised]
        return cls(idf, doc_vectors)

    @staticmethod
    def _vectorize(terms: list, idf: dict) -> dict:
        weights = {
            term: count * idf[term]
            for term, count in Counter(terms).items()
            if term in idf
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        if norm > 0:
            weights = {term: w / norm for term, w in weights.items()}
        return weights

    def transform_query(self, query: str) -> dict:
        return self._vectorize(_analyze(query), self._idf)

    def similarities(self, query_vector: dict) -> list:
        if not query_vector:
            return [0.0] * len(self._doc_vectors)
        return [
            sum(weight * doc.get(term, 0.0) for term, weight in query_vector.items())
            for doc in self._doc_vectors
        ]


@lru_cache(maxsize=1)
def _load_variable_aliases() -> dict:
    """Invert arco_overrides' short->long variable alias map to long-form
    names per short variable, e.g. "t2m" -> ["2m temperature"]."""
    if not _OVERRIDES_PATH.exists():
        return {}
    with _OVERRIDES_PATH.open(encoding="utf-8") as fp:
        data = json.load(fp) or {}

    aliases: dict = {}
    overrides = data.get("overrides", {})
    for long_name, short_name in overrides.get("variable_aliases", {}).items():
        aliases.setdefault(short_name, []).append(long_name.replace("_", " "))
    for key, value in overrides.items():
        if key == "variable_aliases" or not isinstance(value, dict):
            continue
        for long_name, short_name in value.get("variable_aliases", {}).items():
            aliases.setdefault(short_name, []).append(long_name.replace("_", " "))
    return aliases


def _build_corpus_entries() -> list:
    aliases = _load_variable_aliases()
    datasets = _load_arco_snapshot()

    entries = []
    for dataset_id, dataset in datasets.items():
        dataset_title = dataset.get("title", "")
        dataset_description = dataset.get("description", "")
        for subset in dataset.get("subsets", {}).values():
            subset_title = subset.get("title", "")
            for var_name, var_data in subset.get("variables", {}).items():
                parts = [
                    dataset_title,
                    dataset_description,
                    subset_title,
                    var_data.get("name", ""),
                    var_data.get("standard_name", "") or "",
                    var_name.replace("_", " "),
                    *aliases.get(var_name, []),
                ]
                text = " ".join(p for p in parts if p and p != "unknown")
                entries.append(CorpusEntry(dataset_id=dataset_id, variable=var_name, text=text))
    return entries


@lru_cache(maxsize=1)
def _load_index():
    entries = _build_corpus_entries()
    if not entries:
        return entries, None
    model = _TfidfModel.fit([e.text for e in entries])
    return entries, model


def query_catalog(query: str, top_k: int = 15) -> list:
    """Rank (dataset_id, variable) pairs by TF-IDF cosine similarity to
    *query*. Returns up to top_k unique pairs sorted by descending score."""
    entries, model = _load_index()
    if not entries or model is None:
        return []

    scores = model.similarities(model.transform_query(query))

    best: dict = {}
    for entry, score in zip(entries, scores):
        if score <= 0:
            continue
        key = (entry.dataset_id, entry.variable)
        if score > best.get(key, 0.0):
            best[key] = float(score)

    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    return [(dataset_id, variable, score) for (dataset_id, variable), score in ranked[:top_k]]
