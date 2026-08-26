"""API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Document(BaseModel):
    id: str
    filename: str
    size_bytes: int
    chunks: int
    language: str
    language_name: str
    pages: int | None = None
    added_at: str


class Source(BaseModel):
    ref: int = Field(description="Citation number used in the answer text, 1-based.")
    doc_id: str
    filename: str
    location: str = Field(default="", description="Human-readable page or heading.")
    page: int | None = None
    section: str | None = None
    score: float
    text: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    doc_ids: list[str] | None = Field(
        default=None, description="Restrict retrieval to these documents. None = all."
    )
    profile: str | None = Field(default=None, description="fast | balanced | quality")
    model: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    history: list[dict] = Field(default_factory=list)


class AnswerMeta(BaseModel):
    model: str
    profile: str
    language: str
    chunks_used: int
    elapsed_ms: int


class Answer(BaseModel):
    question: str
    answer: str
    sources: list[Source]
    meta: AnswerMeta


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    doc_ids: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class ProfileInfo(BaseModel):
    name: str
    top_k: int
    rerank: bool
    description: str


class Health(BaseModel):
    status: str
    provider: str = Field(description="ollama | openai | anthropic -- the wire format in use.")
    llm_reachable: bool
    llm_local: bool = Field(description="True when the model runs on this machine.")
    llm_base_url: str
    llm_model: str
    embedding_model: str
    documents: int
    chunks: int
    languages: dict[str, str]
    profiles: list[ProfileInfo]


class ErrorResponse(BaseModel):
    detail: str
