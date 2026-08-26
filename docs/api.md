# HTTP API

Base URL: `http://localhost:8000`. Interactive docs: `/docs` (Swagger) and
`/redoc`. The OpenAPI schema lives at `/openapi.json`, which is enough to
generate a client in any language.

There is no authentication. The server binds to `127.0.0.1` by default and is
meant to stay on your machine. If you set `GLOSSA_HOST=0.0.0.0` to reach it from
another device, put it behind a reverse proxy that handles auth.

---

## Meta

### `GET /api/health`

```json
{
  "status": "ok",
  "provider": "ollama",
  "llm_reachable": true,
  "llm_local": true,
  "llm_base_url": "http://localhost:11434",
  "llm_model": "ornith:9b",
  "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
  "documents": 3,
  "chunks": 214,
  "languages": { "uk": "Українська", "en": "English" },
  "profiles": [
    { "name": "fast", "top_k": 3, "rerank": false, "description": "..." }
  ]
}
```

`provider` is the wire format in use (`ollama`, `openai` or `anthropic`) and
`llm_local` says whether the model runs on this machine. `llm_reachable: false`
means the model is unreachable, or a hosted provider has no API key set;
retrieval still works and generation returns `503`.

### `GET /api/languages`

Every supported language code and native name, plus the count.

### `GET /api/models`

```json
{
  "current": "ornith:9b",
  "provider": "ollama",
  "available": ["ornith:9b", "qwen3.5:9b"]
}
```

Returns an empty `available` list when the endpoint is unreachable or the key
is missing.

---

## Documents

### `GET /api/documents`

```json
[
  {
    "id": "9f2c4a1b8e7d6c5f",
    "filename": "contract.pdf",
    "size_bytes": 148213,
    "chunks": 61,
    "language": "uk",
    "language_name": "Українська",
    "pages": 12,
    "added_at": "2026-08-25T10:14:00+00:00"
  }
]
```

`id` is a content hash, so uploading the same bytes twice gives the same id and
replaces the previous copy.

### `POST /api/documents`

`multipart/form-data`, field name `files`, repeatable.

```bash
curl -X POST http://localhost:8000/api/documents \
  -F "files=@contract.pdf" \
  -F "files=@appendix.docx"
```

Returns the list of created documents.

| Status | Cause |
|---|---|
| `413` | File exceeds `GLOSSA_MAX_UPLOAD_MB` |
| `415` | Extension is not `.pdf`, `.docx`, `.txt`, `.md` |
| `422` | Detected as Russian, or the file has no extractable text |

### `DELETE /api/documents/{id}`

Removes the document, its vectors and its stored copy. `404` if unknown.

### `DELETE /api/documents`

Wipes the library. No confirmation, so guard it in any UI you build.

---

## Query

### `POST /api/ask`

```json
{
  "question": "Які терміни оплати?",
  "doc_ids": ["9f2c4a1b8e7d6c5f"],
  "profile": "balanced",
  "model": "ornith:9b",
  "top_k": 5,
  "history": [{ "role": "user", "content": "..." }]
}
```

Every field except `question` is optional. `doc_ids: null` searches everything.

Response:

```json
{
  "question": "Які терміни оплати?",
  "answer": "Оплата протягом 30 днів [1].",
  "sources": [
    {
      "ref": 1,
      "doc_id": "9f2c4a1b8e7d6c5f",
      "filename": "contract.pdf",
      "location": "p. 4",
      "page": 4,
      "section": null,
      "score": 0.8123,
      "text": "Замовник сплачує..."
    }
  ],
  "meta": {
    "model": "ornith:9b",
    "profile": "balanced",
    "language": "uk",
    "chunks_used": 5,
    "elapsed_ms": 4210
  }
}
```

The `ref` numbers match the `[n]` markers inside `answer`.

| Status | Cause |
|---|---|
| `422` | Question detected as Russian |
| `503` | LLM server unreachable or returned an error |

When nothing matches, you still get `200` with an empty `sources` array and an
answer saying the documents do not cover the question.

### `POST /api/ask/stream`

Same request body. Returns Server-Sent Events. Three event types arrive in
order:

```
data: {"type":"sources","sources":[...]}

data: {"type":"token","text":"Оплата "}

data: {"type":"token","text":"протягом 30 днів [1]."}

data: {"type":"done","meta":{...}}
```

Errors arrive in-band as `{"type":"error","detail":"..."}` with HTTP `200`,
because the status line has already been sent by then.

Sources arrive **before** the first token, so a UI can render citations while
the answer is still being written.

```js
const response = await fetch("/api/ask/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "Які терміни оплати?" }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const frames = buffer.split("\n\n");
  buffer = frames.pop();            // keep the partial tail
  for (const frame of frames) {
    const line = frame.split("\n").find((l) => l.startsWith("data: "));
    if (line) console.log(JSON.parse(line.slice(6)));
  }
}
```

### `POST /api/search`

Retrieval without generation. The fastest way to tell a retrieval problem from
a model problem.

```json
{ "query": "терміни оплати", "doc_ids": null, "top_k": 5 }
```

Returns the same `Source` objects that `/api/ask` embeds.

### `POST /api/export?format=markdown|json`

Takes an answer payload (exactly what `/api/ask` returned) and gives back a
downloadable file with a `Content-Disposition` header.

```bash
curl -s -X POST localhost:8000/api/ask \
     -H 'Content-Type: application/json' \
     -d '{"question":"Які терміни оплати?"}' \
  | curl -s -X POST 'localhost:8000/api/export?format=markdown' \
         -H 'Content-Type: application/json' --data-binary @- \
  > answer.md
```

---

## Errors

Every failure is FastAPI standard:

```json
{ "detail": "Language 'Русский' (ru) is not supported by this project. Supported: ar, bg, cs, ..." }
```

| Status | Meaning |
|---|---|
| `400` | Empty file |
| `404` | Unknown document id |
| `413` | Upload too large |
| `415` | Unsupported file extension |
| `422` | Unsupported language, unreadable document, or invalid body |
| `503` | LLM unreachable |

## CORS

Controlled by `GLOSSA_CORS_ORIGINS` (comma-separated). The default allows the Vite
dev server on port 5173. A production build served by FastAPI itself is
same-origin and needs no entry.
