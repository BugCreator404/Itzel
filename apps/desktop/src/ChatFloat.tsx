/**
 * ChatFloat — ventana flotante de chat (label: "chat").
 * Sin decoraciones del SO. Usa data-tauri-drag-region para arrastrar.
 * Posición persistida en ~/.itzel/itzel.config.json vía comandos Rust.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { useChatStream } from "./hooks/useChatStream";
import { useMascotaMood } from "./hooks/useMascotaMood";
import { Mascota } from "./components/Mascota";
import { CommandPalette } from "./components/CommandPalette";
import { useChatHistory } from "./hooks/useChatHistory";
import { useShortcuts } from "./hooks/useShortcuts";

// ─── tipos ────────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface SavedPos { x: number; y: number }

// ─── constantes ───────────────────────────────────────────────────────────────

// Debounce para no saturar el FS al arrastrar la ventana
const SAVE_POS_DELAY_MS = 600;

// ─── componente ──────────────────────────────────────────────────────────────

export function ChatFloat() {
  const chat    = useChatStream();
  const { mood, connected, wsError, resetInactivity } = useMascotaMood();

  const [messages,       setMessages]       = useState<Message[]>([]);
  const [input,          setInput]          = useState("");
  const [paletteOpen,    setPaletteOpen]    = useState(false);

  const bottomRef  = useRef<HTMLDivElement>(null);
  const inputRef   = useRef<HTMLTextAreaElement>(null);
  const savePosRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Navegación del historial de mensajes con ↑↓
  const { navigateUp, navigateDown, resetHistory, pushHistory } = useChatHistory(
    messages.filter(m => m.role === "user").map(m => m.content)
  );

  // ── posición de ventana ────────────────────────────────────────────────────

  useEffect(() => {
    (async () => {
      const win = getCurrentWindow();

      // 1. Restaurar posición guardada o colocar en esquina inferior-derecha
      try {
        const saved = await invoke<SavedPos | null>("load_chat_position");
        if (saved) {
          await win.setPosition(new PhysicalPosition(saved.x, saved.y));
        } else {
          // Posición por defecto: esquina inferior-derecha
          const { availWidth, availHeight } = window.screen;
          await win.setPosition(
            new PhysicalPosition(availWidth - 410, availHeight - 560)
          );
        }
      } catch (e) {
        console.warn("[itzel] No se pudo restaurar posición de ventana:", e);
      }

      // 2. Escuchar evento de movimiento → guardar con debounce
      const unlisten = await win.onMoved(({ payload }) => {
        if (savePosRef.current) clearTimeout(savePosRef.current);
        savePosRef.current = setTimeout(() => {
          invoke("save_chat_position", { x: payload.x, y: payload.y }).catch(
            () => {}
          );
        }, SAVE_POS_DELAY_MS);
      });

      return () => {
        unlisten();
        if (savePosRef.current) clearTimeout(savePosRef.current);
      };
    })();
  }, []);

  // ── scroll automático ──────────────────────────────────────────────────────

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.buffer, messages]);

  // ── mover buffer al historial cuando termina el stream ─────────────────────

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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.status]);

  // ── shortcuts globales (Tauri events) ─────────────────────────────────────

  useShortcuts({
    "stop-agent":      () => chat.cancel(),
    "command-palette": () => setPaletteOpen(p => !p),
  });

  // ── acciones del chat ──────────────────────────────────────────────────────

  const handleSend = useCallback(() => {
    const msg = input.trim();
    if (!msg || chat.status === "streaming") return;

    pushHistory(msg);
    resetHistory();
    resetInactivity();
    setMessages(prev => [...prev, { id: crypto.randomUUID(), role: "user", content: msg }]);
    setInput("");
    chat.send(msg);
  }, [input, chat, pushHistory, resetHistory, resetInactivity]);

  const handleClear = useCallback(() => {
    setMessages([]);
    chat.reset();
    setInput("");
    resetHistory();
  }, [chat, resetHistory]);

  const handleClose = useCallback(async () => {
    await getCurrentWindow().hide();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter → enviar
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
        return;
      }
      // ↑ → historial anterior
      if (e.key === "ArrowUp" && !input) {
        const prev = navigateUp();
        if (prev !== null) setInput(prev);
        return;
      }
      // ↓ → historial siguiente
      if (e.key === "ArrowDown") {
        const next = navigateDown();
        setInput(next ?? "");
        return;
      }
      // Ctrl+K → limpiar conversación
      if (e.key === "k" && e.ctrlKey) {
        e.preventDefault();
        handleClear();
        return;
      }
      // Ctrl+M → abrir command palette filtrado en modelos
      if (e.key === "m" && e.ctrlKey) {
        e.preventDefault();
        setPaletteOpen(true);
        return;
      }
      // Ctrl+/ → command palette
      if (e.key === "/" && e.ctrlKey) {
        e.preventDefault();
        setPaletteOpen(p => !p);
      }
    },
    [input, handleSend, handleClear, navigateUp, navigateDown]
  );

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="chatfloat">

      {/* ── Barra superior (drag handle) ─────────────────────────── */}
      <div className="chatfloat-bar" data-tauri-drag-region>
        <span className="chatfloat-logo" data-tauri-drag-region>
          ITZEL
        </span>
        <div className="chatfloat-bar-right" data-tauri-drag-region>
          <span
            className={`status-dot ${connected ? "status-dot--on" : "status-dot--off"}`}
            title={connected ? "Backend conectado" : "Backend desconectado"}
          />
          <button
            className="chatfloat-close"
            onClick={handleClose}
            title="Ocultar (Ctrl+Space para abrir)"
            aria-label="Cerrar ventana flotante"
          >
            ×
          </button>
        </div>
      </div>

      {/* ── Error de conexión ─────────────────────────────────────── */}
      {wsError && (
        <div className="chatfloat-error">
          ⚠ {wsError}
        </div>
      )}

      {/* ── Mascota (esquina superior-derecha sobre los mensajes) ─── */}
      <div className="chatfloat-mascota">
        <Mascota mood={mood} size={0.55} interactive={false} />
      </div>

      {/* ── Historial de mensajes ─────────────────────────────────── */}
      <main className="chatfloat-messages">
        {messages.length === 0 && (
          <div className="chatfloat-empty">
            <p>Hola, soy Itzel 🦎</p>
            <p className="chatfloat-empty-sub">
              {connected ? "Lista para ayudarte" : "Conectando al backend…"}
            </p>
          </div>
        )}

        {messages.map(m => (
          <div key={m.id} className={`msg msg--${m.role}`}>
            <span className="msg-label">{m.role === "user" ? "Tú" : "Itzel"}</span>
            <p className="msg-content">{m.content}</p>
          </div>
        ))}

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

      {/* ── Input ─────────────────────────────────────────────────── */}
      <footer className="chatfloat-footer">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          onChange={e => { setInput(e.target.value); resetInactivity(); }}
          onKeyDown={handleKeyDown}
          placeholder="Escribe… (↑ historial · Ctrl+K limpiar · Ctrl+/ acciones)"
          rows={2}
          disabled={chat.status === "streaming"}
          aria-label="Mensaje para Itzel"
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
              disabled={!input.trim() || !connected}
            >
              Enviar
            </button>
          )}
          <button
            className="btn btn--ghost"
            onClick={handleClear}
            title="Ctrl+K"
            aria-label="Limpiar conversación"
          >
            ✕
          </button>
        </div>
      </footer>

      {/* ── Command Palette ───────────────────────────────────────── */}
      {paletteOpen && (
        <CommandPalette onClose={() => setPaletteOpen(false)} />
      )}

    </div>
  );
}
