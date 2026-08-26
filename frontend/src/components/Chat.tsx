import { useEffect, useRef, useState } from "react";
import { api, type Source } from "../api";
import { Message, type ChatTurn } from "./Message";
import { SendIcon, SparkIcon, StopIcon } from "./Icons";

interface Props {
  docCount: number;
  selected: string[];
  profile: string;
  model: string;
  topK: number;
  onError: (message: string) => void;
}

const EXAMPLES = [
  "What is this document about, in three sentences?",
  "Which dates and figures does it mention?",
  "Про що цей документ? Дай відповідь українською.",
];

export function Chat({ docCount, selected, profile, model, topK, onError }: Props) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  function send(question: string) {
    if (!question.trim() || busy) return;

    const id = crypto.randomUUID();
    const history = turns
      .filter((turn) => !turn.error)
      .slice(-6)
      .map((turn) => ({ role: turn.role, content: turn.text }));

    setTurns((previous) => [
      ...previous,
      { id: `${id}-q`, role: "user", text: question },
      { id, role: "assistant", text: "", question, streaming: true },
    ]);
    setDraft("");
    setBusy(true);

    const patch = (change: Partial<ChatTurn>) =>
      setTurns((previous) =>
        previous.map((turn) => (turn.id === id ? { ...turn, ...change } : turn)),
      );

    abort.current = api.askStream(
      question,
      { docIds: selected, profile, model: model || undefined, topK, history },
      {
        onSources: (sources: Source[]) => patch({ sources }),
        onToken: (text) =>
          setTurns((previous) =>
            previous.map((turn) =>
              turn.id === id ? { ...turn, text: turn.text + text } : turn,
            ),
          ),
        onDone: (meta) => {
          patch({ meta, streaming: false });
          setBusy(false);
        },
        onError: (detail) => {
          patch({ text: detail, streaming: false, error: true });
          setBusy(false);
          onError(detail);
        },
      },
    );
  }

  function stop() {
    abort.current?.abort();
    setBusy(false);
    setTurns((previous) =>
      previous.map((turn) => (turn.streaming ? { ...turn, streaming: false } : turn)),
    );
  }

  return (
    <section className="chat">
      <div className="messages">
        {turns.length === 0 && (
          <div className="welcome">
            <SparkIcon size={28} />
            <h2>Ask your documents</h2>
            <p>
              {docCount === 0
                ? "Add a PDF, DOCX, TXT or Markdown file on the left to begin."
                : `${docCount} document(s) ready. Every answer cites its source.`}
            </p>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} disabled={docCount === 0} onClick={() => send(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <Message key={turn.id} turn={turn} />
        ))}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          send(draft);
        }}
      >
        <textarea
          ref={box}
          value={draft}
          rows={1}
          placeholder={
            docCount === 0 ? "Add a document to begin…" : "Ask anything…"
          }
          disabled={docCount === 0}
          onChange={(event) => {
            setDraft(event.target.value);
            const element = event.target;
            element.style.height = "auto";
            element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send(draft);
            }
          }}
        />
        <button
          type={busy ? "button" : "submit"}
          className="send"
          onClick={busy ? stop : undefined}
          disabled={!busy && (!draft.trim() || docCount === 0)}
          title={busy ? "Stop" : "Send (Enter)"}
        >
          {busy ? <StopIcon /> : <SendIcon />}
        </button>
      </form>
    </section>
  );
}
