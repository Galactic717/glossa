import { useRef, useState } from "react";
import { api, formatBytes, type Doc } from "../api";
import { FileIcon, TrashIcon, UploadIcon } from "./Icons";

interface Props {
  docs: Doc[];
  selected: string[];
  onSelect: (ids: string[]) => void;
  onChanged: () => void;
  onError: (message: string) => void;
}

const ACCEPT = ".pdf,.txt,.md,.markdown,.docx";

export function Sidebar({ docs, selected, onSelect, onChanged, onError }: Props) {
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function upload(files: FileList | File[] | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    try {
      await api.upload(Array.from(files));
      onChanged();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(id: string) {
    try {
      await api.remove(id);
      onSelect(selected.filter((value) => value !== id));
      onChanged();
    } catch (error) {
      onError((error as Error).message);
    }
  }

  function toggle(id: string) {
    onSelect(selected.includes(id) ? selected.filter((v) => v !== id) : [...selected, id]);
  }

  return (
    <aside className="sidebar">
      <div
        className={`dropzone${dragging ? " dragging" : ""}${busy ? " busy" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void upload(event.dataTransfer.files);
        }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => event.key === "Enter" && input.current?.click()}
      >
        <UploadIcon />
        <strong>{busy ? "Indexing…" : "Drop documents here"}</strong>
        <span>PDF · DOCX · TXT · MD</span>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(event) => void upload(event.target.files)}
        />
      </div>

      <div className="sidebar-head">
        <span>Library</span>
        {docs.length > 0 && (
          <button
            className="link"
            onClick={() => onSelect(selected.length === docs.length ? [] : docs.map((d) => d.id))}
          >
            {selected.length === docs.length ? "clear all" : "select all"}
          </button>
        )}
      </div>

      <div className="doc-list">
        {docs.length === 0 && <p className="empty">Nothing indexed yet.</p>}
        {docs.map((doc) => (
          <div
            key={doc.id}
            className={`doc${selected.includes(doc.id) ? " active" : ""}`}
            onClick={() => toggle(doc.id)}
          >
            <input type="checkbox" checked={selected.includes(doc.id)} readOnly tabIndex={-1} />
            <FileIcon />
            <div className="doc-body">
              <span className="doc-name" title={doc.filename}>
                {doc.filename}
              </span>
              <span className="doc-meta">
                {doc.language_name} · {doc.chunks} chunks · {formatBytes(doc.size_bytes)}
                {doc.pages ? ` · ${doc.pages} pp.` : ""}
              </span>
            </div>
            <button
              className="icon-button danger"
              title="Remove"
              onClick={(event) => {
                event.stopPropagation();
                void remove(doc.id);
              }}
            >
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>

      {selected.length > 0 && (
        <p className="scope-note">
          Search limited to {selected.length} of {docs.length}
        </p>
      )}
    </aside>
  );
}
