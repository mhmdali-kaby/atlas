"""Local 384-d text embeddings for clustering and novelty (brief §7.2 step 5, decision #243).

Two implementations of :class:`Embedder`:

* :class:`SentenceTransformerEmbedder` — the real model (``news.embedding.model`` in defaults.yaml,
  ``all-MiniLM-L6-v2``). The weights are cached under ``news.embedding.cache_dir`` (default
  ``~/Library/Caches/atlas/models``, overridable with ``ATLAS_MODEL_CACHE``), never inside the
  repository, and loaded lazily on the first call, once per process.
* :class:`FakeEmbedder` — deterministic feature hashing of words and character trigrams. No model,
  no network: every unit test and CI run uses it.

Both return L2-normalised vectors, so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from atlas_shared.defaults import DEFAULTS

DIM = 384
"""news_items.embedding is vector(384)."""

CACHE_ENV = "ATLAS_MODEL_CACHE"


@runtime_checkable
class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One L2-normalised vector of ``dim`` floats per text, in order."""
        ...


def embedding_settings() -> dict[str, Any]:
    cfg: dict[str, Any] = DEFAULTS["news"]["embedding"]
    return cfg


def model_cache_dir() -> Path:
    """Where model weights live: ``$ATLAS_MODEL_CACHE``, else defaults.yaml ``news.embedding.cache_dir``."""
    raw = os.environ.get(CACHE_ENV) or str(embedding_settings()["cache_dir"])
    return Path(raw).expanduser()


def model_is_cached(model_name: str | None = None, cache_dir: Path | None = None) -> bool:
    """True when the model's weights are already on disk, so loading needs no network."""
    name = model_name or str(embedding_settings()["model"])
    root = cache_dir or model_cache_dir()
    repo = name if "/" in name else f"sentence-transformers/{name}"
    snapshots = root / f"models--{repo.replace('/', '--')}" / "snapshots"
    if not snapshots.is_dir():
        return False
    return any((snap / "config.json").exists() for snap in snapshots.iterdir())


def item_text(title: str, summary: str | None = None) -> str:
    """The text a news item is embedded from: the title, plus the summary when there is one."""
    title = title.strip()
    summary = (summary or "").strip()
    return f"{title}. {summary}" if summary else title


def _normalise(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [float(v) / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors (normalised or not)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


_WORD = re.compile(r"\w+", re.UNICODE)


class FakeEmbedder:
    """Deterministic stand-in for the real model: hashed bag of words and character trigrams.

    Identical texts give identical vectors (cosine 1.0); texts sharing most words are close;
    unrelated texts are near-orthogonal. Stable across processes (sha1, not ``hash()``).
    """

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.name = "fake"

    def _features(self, text: str) -> list[tuple[str, float]]:
        words = [w.lower() for w in _WORD.findall(text)]
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        for w in words:
            padded = f"#{w}#"
            feats.extend((f"g:{padded[i : i + 3]}", 0.3) for i in range(max(len(padded) - 2, 1)))
        return feats

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, weight in self._features(text):
            digest = hashlib.sha1(feat.encode("utf-8"), usedforsecurity=False).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * weight
        return _normalise(vec)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class SentenceTransformerEmbedder:
    """``sentence-transformers`` model, loaded once on first use (thread-safe)."""

    def __init__(
        self,
        model_name: str | None = None,
        cache_dir: Path | None = None,
        *,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name or str(embedding_settings()["model"])
        self.cache_dir = cache_dir or model_cache_dir()
        self.device = device or str(embedding_settings().get("device", "cpu"))
        self.dim = DIM
        self.name = self.model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    # Once the weights are on disk, never touch the network again.
                    model = SentenceTransformer(
                        self.model_name,
                        cache_folder=str(self.cache_dir),
                        device=self.device,
                        local_files_only=model_is_cached(self.model_name, self.cache_dir),
                    )
                    dim_of = (
                        getattr(model, "get_embedding_dimension", None)
                        or model.get_sentence_embedding_dimension
                    )
                    got = dim_of()
                    if got != self.dim:
                        raise ValueError(f"{self.model_name} gives {got}-d vectors, news_items needs {DIM}")
                    self._model = model
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return [[float(v) for v in row] for row in vectors]


_default: Embedder | None = None
_default_lock = threading.Lock()


def get_default_embedder() -> Embedder:
    """The process-wide embedder: the real model unless :func:`set_default_embedder` replaced it."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = SentenceTransformerEmbedder()
    return _default


def current_default_embedder() -> Embedder | None:
    """The process-wide embedder if one was chosen or created already, without creating one."""
    return _default


def set_default_embedder(embedder: Embedder | None) -> None:
    """Replace the process-wide embedder (tests, the report's ``--embedder`` flag). None resets it."""
    global _default
    _default = embedder


def make_embedder(kind: str) -> Embedder:
    """``real`` → :class:`SentenceTransformerEmbedder` (downloads the model once if it is not cached),
    ``fake`` → :class:`FakeEmbedder`, ``auto`` → the real model only when it is already cached."""
    if kind == "auto":
        kind = "real" if model_is_cached() else "fake"
    if kind == "real":
        return SentenceTransformerEmbedder()
    if kind == "fake":
        return FakeEmbedder()
    raise ValueError(f"unknown embedder {kind!r}; use 'real', 'fake' or 'auto'")
