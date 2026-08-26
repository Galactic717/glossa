export interface Doc {
  id: string;
  filename: string;
  size_bytes: number;
  chunks: number;
  language: string;
  language_name: string;
  pages: number | null;
  added_at: string;
}

export interface Source {
  ref: number;
  doc_id: string;
  filename: string;
  location: string;
  page: number | null;
  section: string | null;
  score: number;
  text: string;
}

export interface Meta {
  model: string;
  profile: string;
  language: string;
  chunks_used: number;
  elapsed_ms: number;
}

export interface Answer {
  question: string;
  answer: string;
  sources: Source[];
  meta: Meta;
}

export interface ProfileInfo {
  name: string;
  top_k: number;
  rerank: boolean;
  description: string;
}

export interface Health {
  status: string;
  provider: string;
  llm_reachable: boolean;
  llm_local: boolean;
  llm_base_url: string;
  llm_model: string;
  embedding_model: string;
  documents: number;
  chunks: number;
  languages: Record<string, string>;
  profiles: ProfileInfo[];
}

export interface AskOptions {
  docIds?: string[] | null;
  profile?: string;
  model?: string;
  topK?: number;
  history?: { role: string; content: string }[];
}

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* body was not json */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  models: () =>
    request<{ current: string; provider: string; available: string[] }>("/api/models"),
  documents: () => request<Doc[]>("/api/documents"),

  upload(files: File[]): Promise<Doc[]> {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return request<Doc[]>("/api/documents", { method: "POST", body });
  },

  remove: (id: string) => request<unknown>(`/api/documents/${id}`, { method: "DELETE" }),
  clear: () => request<unknown>("/api/documents", { method: "DELETE" }),

  ask: (question: string, options: AskOptions = {}) =>
    request<Answer>("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload(question, options)),
    }),

  /** Streams an answer. Returns the abort handle so the UI can cancel. */
  askStream(
    question: string,
    options: AskOptions,
    handlers: {
      onSources?: (sources: Source[]) => void;
      onToken?: (text: string) => void;
      onDone?: (meta: Meta) => void;
      onError?: (detail: string) => void;
    },
  ): AbortController {
    const controller = new AbortController();

    (async () => {
      try {
        const response = await fetch(`${BASE}/api/ask/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload(question, options)),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`Server returned ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line; keep the partial tail.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const line = frame.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            const event = JSON.parse(line.slice(6));
            if (event.type === "sources") handlers.onSources?.(event.sources);
            else if (event.type === "token") handlers.onToken?.(event.text);
            else if (event.type === "done") handlers.onDone?.(event.meta);
            else if (event.type === "error") handlers.onError?.(event.detail);
          }
        }
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          handlers.onError?.((error as Error).message);
        }
      }
    })();

    return controller;
  },

  async exportAnswer(answer: Answer, format: "markdown" | "json"): Promise<void> {
    const response = await fetch(`${BASE}/api/export?format=${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answer),
    });
    if (!response.ok) throw new Error("Export failed");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `answer.${format === "json" ? "json" : "md"}`;
    link.click();
    URL.revokeObjectURL(url);
  },
};

function payload(question: string, options: AskOptions) {
  return {
    question,
    doc_ids: options.docIds?.length ? options.docIds : null,
    profile: options.profile ?? null,
    model: options.model ?? null,
    top_k: options.topK ?? null,
    history: options.history ?? [],
  };
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
