import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// Importación diferida — solo disponible dentro del webview de Tauri
// En browser/tests, getCurrentWindow lanzaría, así que lo guardamos en una función
async function getWindowLabel(): Promise<string> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    return getCurrentWindow().label;
  } catch {
    // Fuera de Tauri (browser, Vitest) → asumir ventana principal
    return "main";
  }
}

async function boot() {
  const root = document.getElementById("root");
  if (!root) throw new Error("No se encontró el elemento #root");

  const label = await getWindowLabel();

  // Ventana flotante de chat → componente dedicado
  if (label === "chat") {
    const { ChatFloat } = await import("./ChatFloat");
    createRoot(root).render(
      <StrictMode>
        <ChatFloat />
      </StrictMode>
    );
    return;
  }

  // Ventana principal (y cualquier otra ventana futura)
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}

boot();
