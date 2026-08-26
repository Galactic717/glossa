# Setup

## 1. Requirements

| | Minimum | Comfortable |
|---|---|---|
| RAM | 8 GB | 16 GB |
| Disk | 3 GB | 10 GB |
| Python | 3.10 | 3.12 |
| Node (web UI only) | 18 | 20 |

A GPU is optional, and unnecessary if you use a hosted provider.

## 2. Install

```bash
git clone https://github.com/Galactic717/glossa
cd glossa

python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .
```

This installs the backend, the CLI, and a `glossa` command on your PATH.

## 3. Pick a model

Glossa needs one model to write answers. Retrieval and embeddings are always
local and never involve this choice.

### Local (default, nothing to configure)

```bash
ollama pull ornith:9b
```

`ornith:9b` is a 9B reasoning model with a 262k context window — a good default
for a 16 GB machine. Other options:

| Model | RAM | Notes |
|---|---|---|
| `ornith:9b` | ~6 GB | Default. Long context, strong multilingual output. |
| `gemma3:4b` | ~4 GB | Fastest useful option on a small machine. |
| `qwen3:8b` | ~6 GB | Good alternative, similar profile. |
| `gemma3:12b` | ~10 GB | Better answers if you have the memory. |

Change it with one line:

```bash
GLOSSA_MODEL=gemma3:4b
```

### Hosted API

Copy `.env.example` to `.env` and uncomment one block. Three lines each:

```bash
# OpenRouter -- hundreds of models behind one key
GLOSSA_BASE_URL=https://openrouter.ai/api/v1
GLOSSA_MODEL=anthropic/claude-sonnet-5
GLOSSA_API_KEY=sk-or-...
```

```bash
# Anthropic
GLOSSA_BASE_URL=https://api.anthropic.com
GLOSSA_MODEL=claude-sonnet-5
GLOSSA_API_KEY=sk-ant-...
```

```bash
# OpenAI
GLOSSA_BASE_URL=https://api.openai.com/v1
GLOSSA_MODEL=gpt-4o-mini
GLOSSA_API_KEY=sk-...
```

```bash
# Groq, llama.cpp, LM Studio, vLLM, or anything else OpenAI-compatible
GLOSSA_BASE_URL=https://api.groq.com/openai/v1
GLOSSA_MODEL=llama-3.3-70b-versatile
GLOSSA_API_KEY=gsk_...
```

The wire format is inferred from the URL: `anthropic.com` uses the Anthropic
Messages API, a `/v1` URL uses the OpenAI-compatible format, anything else is
treated as Ollama. If you front a provider with a proxy on a neutral hostname,
name the format explicitly:

```bash
GLOSSA_PROVIDER=anthropic
```

`glossa dash` shows which provider is live and whether it is local.

## 4. Run

```bash
glossa
```

That builds the web UI if it has not been built, starts the API and opens
<http://localhost:8000>. API docs live at <http://localhost:8000/docs>.

The first question downloads the embedding model
(`paraphrase-multilingual-MiniLM-L12-v2`, about 120 MB). After that, retrieval
needs no network at all.

### Without the web UI

```bash
glossa add my-document.pdf
glossa chat
```

### Frontend in dev mode

Two terminals:

```bash
glossa serve --no-open
```

```bash
cd frontend && npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to port 8000.

### Docker

```bash
cd frontend && npm install && npm run build && cd ..
docker compose up --build
docker compose exec ollama ollama pull ornith:9b
```

## 5. Configure (optional)

```bash
cp .env.example .env
```

Every value in `.env.example` is already the default. The knobs worth touching:

| Variable | Default | What it does |
|---|---|---|
| `GLOSSA_MODEL` | `ornith:9b` | Which model answers. |
| `GLOSSA_BASE_URL` | `http://localhost:11434` | Where that model lives. |
| `GLOSSA_API_KEY` | — | Required for hosted providers. |
| `GLOSSA_PROVIDER` | `auto` | `ollama`, `openai` or `anthropic` to override detection. |
| `GLOSSA_PROFILE` | `balanced` | `fast`, `balanced` or `quality`. |
| `GLOSSA_CHUNK_SIZE` | `900` | Characters per indexed fragment. |
| `GLOSSA_DATA_DIR` | `./data` | Where vectors and uploads live. |
| `GLOSSA_MAX_UPLOAD_MB` | `50` | Per-file upload limit. |

Older `RAG_*` variable names are still read, so an existing `.env` keeps
working.

## Troubleshooting

**`glossa: command not found` right after installing**
Python put the launcher in a scripts directory that is not on your PATH. Either
add it (`python -c "import sysconfig; print(sysconfig.get_path('scripts'))"`
prints the location), or run the module directly from the repo — it behaves
identically:

```bash
python -m cli.main
```

**`Cannot reach Ollama at http://localhost:11434`**
Ollama is not running. Start it with `ollama serve`, then `glossa dash` to
confirm.

**`openai returned 401: Set GLOSSA_API_KEY`**
The provider needs a key and none was given, or the key was rejected. Hosted
providers show as `offline` in `glossa dash` until a key is set.

**`… returned 404: Check that model 'x' exists on this provider`**
Model names differ per provider. OpenRouter wants `anthropic/claude-sonnet-5`,
Anthropic wants `claude-sonnet-5`. `glossa models` lists what your endpoint
offers.

**First question hangs for a minute**
That is the one-time embedding-model download. Watch the server log.

**`No extractable text in this PDF`**
The PDF is a scan. Run it through OCR first, for example
`ocrmypdf in.pdf out.pdf`.

**The web UI does not appear, only the API**
Node is not installed, so the UI could not be built. Install Node 18+ and run
`glossa` again, or use `glossa chat`.

**Ukrainian text prints as `?????` in the terminal**
Only affects older Windows consoles. The CLI forces UTF-8 on startup; if your
terminal still mangles it, run `chcp 65001` first or use Windows Terminal.

**Answers come back empty, or take three times longer than expected**
You are running a reasoning model (ornith, qwen3, deepseek-r1, gpt-oss). Those
spend their token budget in a separate `thinking` field and leave the answer
empty. Glossa sends `think: false` to Ollama automatically and falls back
silently if a model does not understand the flag. If a model ignores it too,
raise the budget:

```bash
GLOSSA_MAX_TOKENS=2048
```

**The process dies with no traceback while indexing a PDF (Windows)**
`pypdf` pulls in `cryptography`, whose native OpenSSL module crashes the
process if PyTorch is loaded after it. The engine warms the embedding model
before any parser runs, which fixes the order. If you call
`utils.parse_file()` directly in your own script, load the model first.

**Answers are slow**
Switch to `fast`, use a smaller local model, or point Glossa at a hosted API:

```bash
glossa ask "..." --profile fast --model gemma3:4b
```
