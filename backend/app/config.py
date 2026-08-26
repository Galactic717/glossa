"""Runtime settings. Everything is overridable by env var or `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Profile:
    """A speed/quality trade-off preset."""

    name: str
    top_k: int
    candidates: int
    rerank: bool
    max_context_chars: int
    description: str


PROFILES: dict[str, Profile] = {
    "fast": Profile(
        name="fast",
        top_k=3,
        candidates=8,
        rerank=False,
        max_context_chars=4000,
        description="Fewest chunks, no reranking. Answers in a couple of seconds.",
    ),
    "balanced": Profile(
        name="balanced",
        top_k=5,
        candidates=20,
        rerank=False,
        max_context_chars=8000,
        description="Default. MMR-diversified retrieval, no reranker download.",
    ),
    "quality": Profile(
        name="quality",
        top_k=8,
        candidates=40,
        rerank=True,
        max_context_chars=14000,
        description="Wide recall plus a multilingual cross-encoder reranker.",
    ),
}

DEFAULT_PROFILE = "balanced"

# The three wire formats Glossa speaks. `auto` picks one from the base URL.
PROVIDERS = ("auto", "ollama", "openai", "anthropic")

# Ready-made endpoints, so switching provider is one line in `.env`.
PRESETS: dict[str, tuple[str, str]] = {
    "ollama": ("http://localhost:11434", "ornith:9b"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-5"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "llamacpp": ("http://localhost:8080/v1", "local-model"),
    "lmstudio": ("http://localhost:1234/v1", "local-model"),
}


def _env(key: str, default: str) -> str:
    """Read `GLOSSA_<key>`, falling back to the old `RAG_<key>` name."""
    return os.environ.get(f"GLOSSA_{key}") or os.environ.get(f"RAG_{key}") or default


def _load_dotenv() -> None:
    path = Path(os.environ.get("GLOSSA_ENV_FILE", str(_ROOT / ".env")))
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def detect_provider(base_url: str) -> str:
    """Infer the wire format from the endpoint."""
    url = base_url.lower()
    if "anthropic" in url:
        return "anthropic"
    if "/v1" in url:
        return "openai"
    return "ollama"


@dataclass
class Settings:
    # storage
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(_ROOT / "data"))))

    # embeddings -- multilingual MiniLM covers 50+ languages, ~120 MB.
    # Always local: your documents are never sent anywhere to be embedded.
    embedding_model: str = _env(
        "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    reranker_model: str = _env("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")

    # chunking
    chunk_size: int = int(_env("CHUNK_SIZE", "900"))
    chunk_overlap: int = int(_env("CHUNK_OVERLAP", "150"))

    # answering model -- local Ollama by default, any provider by config
    provider: str = _env("PROVIDER", "auto")
    llm_base_url: str = _env("BASE_URL", PRESETS["ollama"][0])
    llm_model: str = _env("MODEL", PRESETS["ollama"][1])
    llm_api_key: str = _env("API_KEY", "")
    temperature: float = float(_env("TEMPERATURE", "0.2"))
    max_tokens: int = int(_env("MAX_TOKENS", "1024"))
    request_timeout: int = int(_env("TIMEOUT", "300"))

    # retrieval
    profile: str = _env("PROFILE", DEFAULT_PROFILE)
    max_upload_mb: int = int(_env("MAX_UPLOAD_MB", "50"))

    # server
    host: str = _env("HOST", "127.0.0.1")
    port: int = int(_env("PORT", "8000"))
    cors_origins: str = _env("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.profile not in PROFILES:
            self.profile = DEFAULT_PROFILE
        if self.provider not in PROVIDERS:
            self.provider = "auto"

    @property
    def resolved_provider(self) -> str:
        return detect_provider(self.llm_base_url) if self.provider == "auto" else self.provider

    @property
    def is_local(self) -> bool:
        return any(host in self.llm_base_url for host in ("localhost", "127.0.0.1", "0.0.0.0"))

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def uploads_dir(self) -> Path:
        path = self.data_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_profile(self, name: str | None = None) -> Profile:
        return PROFILES.get(name or self.profile, PROFILES[DEFAULT_PROFILE])


settings = Settings()
