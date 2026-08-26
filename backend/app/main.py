"""FastAPI application: REST + SSE in front of the RAG engine."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import languages, utils
from .config import PROFILES, settings
from .llm import LLMError
from .models import (
    Answer,
    AskRequest,
    Document,
    Health,
    ProfileInfo,
    SearchRequest,
    Source,
)
from .rag import DocumentNotFound, RAGEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag.api")

app = FastAPI(
    title="Glossa",
    description=(
        "Local, offline Retrieval-Augmented Generation over your own documents. "
        "35 supported languages. Every answer carries citations."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = RAGEngine(settings)


# --------------------------------------------------------------------------- meta


@app.get("/api/health", response_model=Health, tags=["meta"])
def health() -> Health:
    stats = engine.stats()
    return Health(
        status="ok",
        provider=settings.resolved_provider,
        llm_reachable=engine.llm.health(),
        llm_local=settings.is_local,
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        embedding_model=settings.embedding_model,
        documents=stats["documents"],
        chunks=stats["chunks"],
        languages=languages.LANGUAGES,
        profiles=[
            ProfileInfo(
                name=profile.name,
                top_k=profile.top_k,
                rerank=profile.rerank,
                description=profile.description,
            )
            for profile in PROFILES.values()
        ],
    )


@app.get("/api/languages", tags=["meta"])
def supported_languages() -> dict:
    return {"supported": languages.LANGUAGES, "count": len(languages.LANGUAGES)}


@app.get("/api/models", tags=["meta"])
def models() -> dict:
    return {
        "current": settings.llm_model,
        "provider": settings.resolved_provider,
        "available": engine.llm.list_models(),
    }


# --------------------------------------------------------------------------- documents


@app.get("/api/documents", response_model=list[Document], tags=["documents"])
def list_documents() -> list[Document]:
    return [Document(**doc) for doc in engine.list_documents()]


@app.post("/api/documents", response_model=list[Document], tags=["documents"])
async def upload_documents(files: list[UploadFile] = File(...)) -> list[Document]:
    """Upload one or many PDF / DOCX / TXT / MD files."""
    limit = settings.max_upload_mb * 1024 * 1024
    added: list[Document] = []

    for upload in files:
        name = Path(upload.filename or "document").name
        suffix = Path(name).suffix.lower()
        if suffix not in utils.SUPPORTED_SUFFIXES:
            raise HTTPException(
                415,
                f"{name}: unsupported format. Allowed: "
                f"{', '.join(sorted(utils.SUPPORTED_SUFFIXES))}",
            )

        payload = await upload.read()
        if len(payload) > limit:
            raise HTTPException(413, f"{name} is larger than {settings.max_upload_mb} MB.")
        if not payload:
            raise HTTPException(400, f"{name} is empty.")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        try:
            added.append(Document(**engine.add_document(temporary, filename=name)))
        except languages.UnsupportedLanguage as exc:
            raise HTTPException(422, str(exc)) from exc
        except utils.UnsupportedFormat as exc:
            raise HTTPException(422, f"{name}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    return added


@app.delete("/api/documents/{doc_id}", tags=["documents"])
def delete_document(doc_id: str) -> dict:
    try:
        engine.delete_document(doc_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, f"No document with id {doc_id}") from exc
    return {"deleted": doc_id}


@app.delete("/api/documents", tags=["documents"])
def clear_documents() -> dict:
    engine.clear()
    return {"deleted": "all"}


# --------------------------------------------------------------------------- query


@app.post("/api/search", response_model=list[Source], tags=["query"])
def search(request: SearchRequest) -> list[Source]:
    """Retrieval only, no LLM. Useful for debugging relevance."""
    hits = engine.search(request.query, top_k=request.top_k, doc_ids=request.doc_ids)
    return [Source(**hit) for hit in hits]


@app.post("/api/ask", response_model=Answer, tags=["query"])
def ask(request: AskRequest) -> Answer:
    try:
        result = engine.ask(
            request.question,
            doc_ids=request.doc_ids,
            profile_name=request.profile,
            model=request.model,
            top_k=request.top_k,
            history=request.history,
        )
    except languages.UnsupportedLanguage as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Answer(**result)


@app.post("/api/ask/stream", tags=["query"])
def ask_stream(request: AskRequest) -> StreamingResponse:
    """Server-Sent Events: one `sources` event, then `token` events, then `done`."""

    def events():
        try:
            for event in engine.ask_stream(
                request.question,
                doc_ids=request.doc_ids,
                profile_name=request.profile,
                model=request.model,
                top_k=request.top_k,
                history=request.history,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (languages.UnsupportedLanguage, LLMError) as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/export", tags=["query"], response_class=PlainTextResponse)
def export(answer: Answer, format: str = Query("markdown", pattern="^(markdown|json)$")):
    """Turn an answer payload into a downloadable Markdown or JSON file."""
    payload = answer.model_dump()
    if format == "json":
        body, media, extension = utils.answer_to_json(payload), "application/json", "json"
    else:
        body, media, extension = utils.answer_to_markdown(payload), "text/markdown", "md"
    return PlainTextResponse(
        body,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="answer.{extension}"'},
    )


# --------------------------------------------------------------------------- frontend

_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str = "") -> HTMLResponse:
        return HTMLResponse((_DIST / "index.html").read_text(encoding="utf-8"))

else:

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(
            "<h1>Glossa</h1>"
            "<p>API is running. Docs: <a href='/docs'>/docs</a></p>"
            "<p>Build the web UI with <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "or run it in dev mode with <code>npm run dev</code>.</p>"
        )


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
