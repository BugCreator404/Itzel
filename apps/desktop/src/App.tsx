import { useEffect, useRef, useState } from "react";
import { useChatStream } from "./hooks/useChatStream";
import { useWsState } from "./hooks/useWsState";
import "./index.css";

// ── Tipos ──────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

// ── Componentes pequeños ───────────────────────────────────────────────────

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span
      className={`status-dot ${connected ? "status-dot--on" : "status-dot--off"}`}
      title={connected ? "Backend conectado" : "Backend desconectado"}
    />
  );
}

function MascotBadge({ mascot }: { mascot: string }) {
  const labels: Record<string, string> = {
    idle:  "idle",
    work:  "trabajando…",
    think: "pensando…",
    happy: "¡listo!",
    wave:  "¡hola!",
    sleep: "zzz",
  };
  return (
    <span className={`mascot-badge mascot-badge--${mascot}`}>
      🦎 {labels[mascot] ?? mascot}
    </span>
  );
}

// ── App principal ──────────────────────────────────────────────────────────

export default function App() {
  const ws   = useWsState();
  const chat = useChatStream();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input,    setInput]    = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll automático al recibir nuevos tokens
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.buffer, messages]);

  // Cuando el stream termina, mover el buffer al historial
  useEffect(() => {
    if ((chat.status === "done" || chat.status === "error") && chat.buffer) {
      setMessages(prev => [
        ...prev,
        {
          id:      crypto.randomUUID(),
          role:    "assistant",
          content: chat.buffer + (chat.error ? `\n\n⚠ ${chat.error}` : ""),
        },
      ]);
    }
  // Solo cuando cambia el status — no queremos re-disparar por cada token
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.status]);

  // ── Enviar mensaje ─────────────────────────────────────────────
  function handleSend() {
    const msg = input.trim();
    if (!msg || chat.status === "streaming") return;

    setMessages(prev => [...prev, { id: crypto.randomUUID(), role: "user", content: msg }]);
    setInput("");
    chat.send(msg, ws.model);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="app">

      {/* ── Header ────────────────────────────────────────────── */}
      <header className="app-header">
        <span className="app-logo">ITZEL</span>
        <div className="app-header-right">
          <MascotBadge mascot={ws.mascot} />
          <span className="app-model">{ws.model}</span>
          <StatusDot connected={ws.connected} />
        </div>
      </header>

      {/* ── Banner de error de conexión ───────────────────────── */}
      {ws.error && (
        <div className="error-banner">
          <span>⚠ {ws.error}</span>
          <button onClick={ws.reconnect}>Reconectar</button>
        </div>
      )}

      {/* ── Historial de mensajes ─────────────────────────────── */}
      <main className="messages">
        {messages.length === 0 && (
          <div className="messages-empty">
            <p className="messages-empty-title">Hola, soy Itzel 🦎</p>
            <p className="messages-empty-sub">
              Tu IA personal local · {ws.connected ? "Backend listo" : "Conectando…"}
            </p>
          </div>
        )}

        {messages.map(m => (
          <div key={m.id} className={`msg msg--${m.role}`}>
            <span className="msg-label">{m.role === "user" ? "Tú" : "Itzel"}</span>
            <p className="msg-content">{m.content}</p>
          </div>
        ))}

        {/* Token buffer en vivo mientras llega el stream */}
        {chat.status === "streaming" && chat.buffer && (
          <div className="msg msg--assistant msg--streaming">
            <span className="msg-label">Itzel</span>
            <p className="msg-content">
              {chat.buffer}
              <span className="cursor-blink">▌</span>
            </p>
          </div>
        )}

        <div ref={bottomRef} />
      </main>

      {/* ── Input ─────────────────────────────────────────────── */}
      <footer className="app-footer">
        <textarea
          className="chat-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Escribe algo… (Enter para enviar, Shift+Enter para nueva línea)"
          rows={2}
          disabled={chat.status === "streaming"}
        />
        <div className="chat-actions">
          {chat.status === "streaming" ? (
            <button className="btn btn--stop" onClick={chat.cancel}>
              ⏹ Detener
            </button>
          ) : (
            <button
              className="btn btn--send"
              onClick={handleSend}
              disabled={!input.trim() || !ws.connected}
            >
              Enviar
            </button>
          )}
          <button className="btn btn--ghost" onClick={chat.reset} title="Nueva conversación">
            ✕ Nueva
          </button>
        </div>
      </footer>

    </div>
  );
}
