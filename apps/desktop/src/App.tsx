import { useState } from "react";

export default function App() {
  const [msg, setMsg] = useState("Hola, soy Itzel 🦎");

  return (
    <main className="itzel-app">
      <header className="itzel-header">
        <span className="itzel-logo">ITZEL</span>
        <span className="itzel-version">v0.1.0 · dev</span>
      </header>

      <section className="itzel-body">
        <p>{msg}</p>
        <button onClick={() => setMsg("¡Ajolote activado! 🦎✨")}>
          Tocar a Itzel
        </button>
      </section>
    </main>
  );
}
