"""Retrieval-Augmented Generation engine.

Deliberately no orchestration framework: Chroma + sentence-transformers +
one HTTP call is the whole pipeline, and every step here is inspectable.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import languages, utils
from .config import Profile, Settings
from .config import settings as default_settings
from .llm import LLMClient

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a document question-answering assistant.

Rules:
1. Answer ONLY from the numbered context fragments below. Never use outside knowledge.
2. Cite every claim with the fragment number in square brackets, like [1] or [2][3].
3. If the context does not contain the answer, say so plainly, in the language of
   the question. Do not guess and do not apologise at length.
4. Be concise and concrete. Quote exact figures, names and dates from the context.
5. LANGUAGE RULE, overrides everything else: write the entire answer in the
   language named at the end of the user message. Never answer in English
   unless English is the language named there."""

_NO_CONTEXT = {
    "uk": "У завантажених документах немає інформації для відповіді на це питання.",
    "en": "The uploaded documents do not contain information to answer this question.",
}


class DocumentNotFound(KeyError):
    pass


class RAGEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings
        self.llm = LLMClient(self.settings)
        self._lock = threading.Lock()
        self._embedder: Any = None
        self._reranker: Any = None
        self._collection: Any = None
        self._index_path = self.settings.data_dir / "documents.json"

    # ------------------------------------------------------------------ lazy deps

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            log.info("Loading embedding model %s", self.settings.embedding_model)
            self._embedder = SentenceTransformer(self.settings.embedding_model)
        return self._embedder

    @property
    def collection(self) -> Any:
        if self._collection is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(self.settings.chroma_dir))
            # Embeddings are supplied explicitly, so Chroma never downloads its
            # own English-only default model.
            self._collection = client.get_or_create_collection(
                name="documents", metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def _rerank_scores(self, query: str, texts: list[str]) -> list[float] | None:
        try:
            if self._reranker is None:
                from sentence_transformers import CrossEncoder

                log.info("Loading reranker %s", self.settings.reranker_model)
                self._reranker = CrossEncoder(self.settings.reranker_model)
            pairs = [(query, text) for text in texts]
            return [float(score) for score in self._reranker.predict(pairs)]
        except Exception as exc:  # offline, or model not downloaded yet
            log.warning("Reranker unavailable, keeping vector order: %s", exc)
            return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.embedder.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [vector.tolist() for vector in vectors]

    # ------------------------------------------------------------------ index

    def _read_index(self) -> dict[str, dict]:
        if not self._index_path.is_file():
            return {}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("Corrupt document index, starting fresh")
            return {}

    def _write_index(self, index: dict[str, dict]) -> None:
        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_documents(self) -> list[dict]:
        docs = self._read_index().values()
        return sorted(docs, key=lambda doc: doc["added_at"], reverse=True)

    def stats(self) -> dict[str, int]:
        index = self._read_index()
        return {
            "documents": len(index),
            "chunks": sum(doc.get("chunks", 0) for doc in index.values()),
        }

    # ------------------------------------------------------------------ ingestion

    def add_document(self, path: Path, filename: str | None = None) -> dict:
        """Parse, chunk, embed and store one file. Re-adding a file replaces it."""
        path = Path(path)
        filename = filename or path.name
        doc_id = utils.file_id(path)

        # Load the embedding model BEFORE any parser runs. pypdf pulls in
        # cryptography, whose native OpenSSL module segfaults the process on
        # Windows if torch is loaded after it. Warming the model here fixes the
        # order for every caller, since all parsing routes through this method.
        _ = self.embedder

        blocks = utils.parse_file(path)
        sample = "\n".join(block.text for block in blocks[:3])[:2000]
        language = languages.ensure_supported(sample)

        chunks = utils.chunk_blocks(
            blocks, self.settings.chunk_size, self.settings.chunk_overlap
        )
        if not chunks:
            raise utils.UnsupportedFormat("Nothing to index: the document has no text.")

        with self._lock:
            self.delete_document(doc_id, missing_ok=True)

            embeddings = self.embed([chunk.text for chunk in chunks])
            self.collection.add(
                ids=[f"{doc_id}:{chunk.index}" for chunk in chunks],
                documents=[chunk.text for chunk in chunks],
                embeddings=embeddings,
                metadatas=[
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "chunk_index": chunk.index,
                        "page": chunk.page if chunk.page is not None else -1,
                        "section": chunk.section or "",
                        "language": language,
                    }
                    for chunk in chunks
                ],
            )

            pages = max((block.page or 0) for block in blocks) or None
            record = {
                "id": doc_id,
                "filename": filename,
                "size_bytes": path.stat().st_size,
                "chunks": len(chunks),
                "language": language,
                "language_name": languages.name_of(language),
                "pages": pages,
                "added_at": utils.utc_now(),
            }
            index = self._read_index()
            index[doc_id] = record
            self._write_index(index)

            stored = self.settings.uploads_dir / f"{doc_id}{path.suffix.lower()}"
            if path.resolve() != stored.resolve():
                shutil.copy2(path, stored)

        return record

    def delete_document(self, doc_id: str, missing_ok: bool = False) -> None:
        index = self._read_index()
        if doc_id not in index and not missing_ok:
            raise DocumentNotFound(doc_id)
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            log.warning("Chroma delete for %s failed: %s", doc_id, exc)
        if doc_id in index:
            suffix = Path(index[doc_id]["filename"]).suffix.lower()
            (self.settings.uploads_dir / f"{doc_id}{suffix}").unlink(missing_ok=True)
            del index[doc_id]
            self._write_index(index)

    def clear(self) -> None:
        with self._lock:
            for doc_id in list(self._read_index()):
                self.delete_document(doc_id, missing_ok=True)

    # ------------------------------------------------------------------ retrieval

    @staticmethod
    def _mmr(query_vec, candidate_vecs, k: int, diversity: float = 0.3) -> list[int]:
        """Maximal Marginal Relevance: relevance minus redundancy.

        Stops an answer from resting on five near-identical chunks of one page.
        Returns positions into `candidate_vecs`.
        """
        import numpy as np

        vectors = np.asarray(candidate_vecs, dtype="float32")
        query = np.asarray(query_vec, dtype="float32")
        relevance = vectors @ query
        selected: list[int] = []
        remaining = list(range(len(vectors)))

        while remaining and len(selected) < k:
            if not selected:
                best = max(remaining, key=lambda i: float(relevance[i]))
            else:
                chosen = vectors[selected]
                best = max(
                    remaining,
                    key=lambda i: (1 - diversity) * float(relevance[i])
                    - diversity * float((chosen @ vectors[i]).max()),
                )
            selected.append(int(best))
            remaining.remove(best)
        return selected

    def search(
        self,
        query: str,
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        profile: Profile | None = None,
    ) -> list[dict]:
        profile = profile or self.settings.resolve_profile()
        top_k = top_k or profile.top_k
        total = self.collection.count()
        if total == 0:
            return []

        where = {"doc_id": {"$in": doc_ids}} if doc_ids else None
        query_vec = self.embed([query])[0]
        result = self.collection.query(
            query_embeddings=[query_vec],
            n_results=min(profile.candidates, total),
            where=where,
            include=["documents", "metadatas", "distances", "embeddings"],
        )

        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        raw_embeddings = (result.get("embeddings") or [[]])[0]
        embeddings = list(raw_embeddings) if raw_embeddings is not None else []
        if not documents:
            return []

        order = list(range(len(documents)))
        if profile.rerank:
            scores = self._rerank_scores(query, documents)
            if scores is not None:
                order.sort(key=lambda i: scores[i], reverse=True)
        if len(order) > top_k:
            if len(embeddings) == len(documents):
                picked = self._mmr(query_vec, [embeddings[i] for i in order], top_k)
                order = [order[position] for position in picked]
            else:
                order = order[:top_k]

        hits: list[dict] = []
        for ref, i in enumerate(order[:top_k], start=1):
            meta = metadatas[i]
            page = meta.get("page", -1)
            page = None if page in (-1, None) else int(page)
            section = meta.get("section") or None
            hits.append(
                {
                    "ref": ref,
                    "doc_id": meta["doc_id"],
                    "filename": meta["filename"],
                    "page": page,
                    "section": section,
                    "location": f"p. {page}" if page else (section or ""),
                    "score": round(1.0 - float(distances[i]), 4),
                    "text": documents[i],
                }
            )
        return hits

    # ------------------------------------------------------------------ generation

    def _build_messages(
        self,
        question: str,
        hits: list[dict],
        history: list[dict],
        profile: Profile,
        language: str,
    ) -> list[dict]:
        budget = profile.max_context_chars
        per_hit = max(500, budget // max(len(hits), 1))
        parts: list[str] = []
        for hit in hits:
            where = f", {hit['location']}" if hit["location"] else ""
            block = f"[{hit['ref']}] {hit['filename']}{where}\n{hit['text'][:per_hit]}"
            if sum(len(part) for part in parts) + len(block) > budget:
                break
            parts.append(block)

        context = "\n\n".join(parts) if parts else "(no fragments found)"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in history[-6:]:
            if turn.get("role") in {"user", "assistant"} and turn.get("content"):
                messages.append({"role": turn["role"], "content": str(turn["content"])[:4000]})
        # Naming the target language beats relying on the model to infer it.
        # Smaller local models drift to English on non-English questions.
        target = f"{languages.name_of(language)} ({language})"
        messages.append(
            {
                "role": "user",
                "content": (
                    f"CONTEXT FRAGMENTS:\n{context}\n\n"
                    f"QUESTION: {question}\n\n"
                    f"Write the entire answer in {target}, and in no other language."
                ),
            }
        )
        return messages

    def _meta(
        self,
        model: str | None,
        profile: Profile,
        language: str,
        hits: int,
        started: float,
    ) -> dict:
        return {
            "model": model or self.settings.llm_model,
            "profile": profile.name,
            "language": language,
            "chunks_used": hits,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def ask(
        self,
        question: str,
        doc_ids: list[str] | None = None,
        profile_name: str | None = None,
        model: str | None = None,
        top_k: int | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        started = time.perf_counter()
        language = languages.ensure_supported(question)
        profile = self.settings.resolve_profile(profile_name)
        hits = self.search(question, top_k=top_k, doc_ids=doc_ids, profile=profile)

        if not hits:
            answer = _NO_CONTEXT.get(language, _NO_CONTEXT["en"])
        else:
            messages = self._build_messages(question, hits, history or [], profile, language)
            answer = self.llm.chat(messages, model=model)

        return {
            "question": question,
            "answer": answer,
            "sources": hits,
            "meta": self._meta(model, profile, language, len(hits), started),
        }

    def ask_stream(
        self,
        question: str,
        doc_ids: list[str] | None = None,
        profile_name: str | None = None,
        model: str | None = None,
        top_k: int | None = None,
        history: list[dict] | None = None,
    ) -> Iterator[dict]:
        """Yields events: sources, then token per piece, then done."""
        started = time.perf_counter()
        language = languages.ensure_supported(question)
        profile = self.settings.resolve_profile(profile_name)
        hits = self.search(question, top_k=top_k, doc_ids=doc_ids, profile=profile)
        yield {"type": "sources", "sources": hits}

        if not hits:
            yield {"type": "token", "text": _NO_CONTEXT.get(language, _NO_CONTEXT["en"])}
        else:
            messages = self._build_messages(question, hits, history or [], profile, language)
            for piece in self.llm.stream(messages, model=model):
                yield {"type": "token", "text": piece}

        yield {"type": "done", "meta": self._meta(model, profile, language, len(hits), started)}
