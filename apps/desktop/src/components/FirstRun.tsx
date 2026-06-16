/**
 * FirstRun — pantalla de bienvenida que aparece la primera vez que se abre Itzel.
 *
 * Tour de 3 pasos:
 *   1. Chat — cómo hablar con Itzel
 *   2. Shortcuts — hotkeys globales
 *   3. Skills — plugins instalados
 *
 * La mascota permanece en mood "wave" durante todo el onboarding.
 * El saludo incluye el nombre del usuario del OS ("¡Hola, Edwin!").
 *
 * English: First-run welcome screen with 3-step tour. Mascot stays in "wave"
 * mood. Greeting uses the OS username.
 */

import { useCallback } from "react";
import { Mascota } from "./Mascota";
import { useFirstRun } from "../hooks/useFirstRun";

// ─── datos del tour ───────────────────────────────────────────────────────────

interface TourStep {
  icon:  string;
  title: string;
  body:  string;
}

const STEPS: TourStep[] = [
  {
    icon:  "💬",
    title: "Chat con Itzel",
    body:
      "Escríbeme cualquier cosa o presiona el hotkey global para activar la " +
      "voz. Soy tu asistente local — todo queda en tu máquina.",
  },
  {
    icon:  "⌨️",
    title: "Atajos de teclado",
    body:
      "Ctrl+Space abre Itzel desde cualquier lugar. " +
      "Ctrl+Shift+I abre la paleta de comandos. " +
      "Configura los atajos en Ajustes → Shortcuts.",
  },
  {
    icon:  "🧩",
    title: "Skills y herramientas",
    body:
      "Itzel viene con skills para buscar en la web, hacer cálculos y manejar " +
      "el portapapeles. Instala más con: itzel skills install <nombre>.",
  },
];

// ─── estilos en línea (sin Tailwind — el proyecto puede no tenerlo) ───────────

const S = {
  overlay: {
    position:        "fixed" as const,
    inset:           0,
    background:      "rgba(10, 5, 20, 0.92)",
    display:         "flex",
    flexDirection:   "column" as const,
    alignItems:      "center",
    justifyContent:  "center",
    zIndex:          9999,
    gap:             "2rem",
    padding:         "2rem",
    backdropFilter:  "blur(8px)",
  },
  card: {
    background:   "#1a0828",
    border:       "1px solid #4a3060",
    borderRadius: "1.25rem",
    padding:      "2rem 2.5rem",
    maxWidth:     "480px",
    width:        "100%",
    textAlign:    "center" as const,
    color:        "#e8e4f4",
    fontFamily:   "system-ui, sans-serif",
  },
  greeting: {
    fontSize:   "1.5rem",
    fontWeight: 700,
    color:      "#f9a8d4",
    marginBottom: "0.5rem",
  },
  subtitle: {
    fontSize:   "0.95rem",
    color:      "#9890b8",
    marginBottom: "1.5rem",
  },
  stepIcon: {
    fontSize: "2.5rem",
    marginBottom: "0.75rem",
    display: "block",
  },
  stepTitle: {
    fontSize:   "1.15rem",
    fontWeight: 600,
    color:      "#f9a8d4",
    marginBottom: "0.5rem",
  },
  stepBody: {
    fontSize:    "0.9rem",
    color:       "#c4bada",
    lineHeight:  1.6,
    marginBottom: "1.75rem",
  },
  dots: {
    display:        "flex",
    justifyContent: "center",
    gap:            "0.5rem",
    marginBottom:   "1.5rem",
  },
  dot: (active: boolean) => ({
    width:        "8px",
    height:       "8px",
    borderRadius: "50%",
    background:   active ? "#f9a8d4" : "#4a3060",
    transition:   "background 0.2s",
  }),
  btn: {
    background:   "#f9a8d4",
    color:        "#1a0828",
    border:       "none",
    borderRadius: "0.75rem",
    padding:      "0.65rem 2rem",
    fontSize:     "0.95rem",
    fontWeight:   700,
    cursor:       "pointer",
    width:        "100%",
    transition:   "opacity 0.15s",
  },
  skip: {
    background:   "transparent",
    color:        "#9890b8",
    border:       "none",
    fontSize:     "0.8rem",
    cursor:       "pointer",
    marginTop:    "0.75rem",
    textDecoration: "underline",
  },
};

// ─── componente ───────────────────────────────────────────────────────────────

interface FirstRunProps {
  /** Callback alternativo si se quiere controlar el estado desde el padre. */
  onComplete?: () => void;
}

export function FirstRun({ onComplete }: FirstRunProps) {
  const { isFirstRun, step, nextStep, completeFirstRun, username } =
    useFirstRun();

  const handleComplete = useCallback(() => {
    completeFirstRun();
    onComplete?.();
  }, [completeFirstRun, onComplete]);

  if (!isFirstRun) return null;

  const current = STEPS[step - 1];
  const isLast  = step === STEPS.length;

  return (
    <div style={S.overlay} role="dialog" aria-modal="true"
         aria-label="Bienvenida a Itzel">

      {/* Mascota en modo wave */}
      <Mascota mood="wave" size={1.2} interactive={false} />

      <div style={S.card}>
        {/* Saludo inicial (solo paso 1) */}
        {step === 1 && (
          <>
            <p style={S.greeting}>¡Hola, {username}!</p>
            <p style={S.subtitle}>
              Soy Itzel, tu IA personal local.{" "}
              <span style={{ color: "#4ecdc4" }}>Sin nube. Sin telemetría.</span>
            </p>
          </>
        )}

        {/* Contenido del paso actual */}
        <span style={S.stepIcon} aria-hidden="true">{current.icon}</span>
        <p style={S.stepTitle}>{current.title}</p>
        <p style={S.stepBody}>{current.body}</p>

        {/* Indicadores de progreso */}
        <div style={S.dots} role="tablist" aria-label="Progreso del tour">
          {STEPS.map((_, i) => (
            <div key={i} style={S.dot(i + 1 === step)}
                 role="tab" aria-selected={i + 1 === step} />
          ))}
        </div>

        {/* Botón de acción */}
        <button
          style={S.btn}
          onClick={isLast ? handleComplete : nextStep}
          aria-label={isLast ? "Empezar a usar Itzel" : "Siguiente paso"}
        >
          {isLast ? "¡Empecemos!" : "Siguiente →"}
        </button>

        {/* Saltar el tour */}
        {!isLast && (
          <button style={S.skip} onClick={handleComplete}
                  aria-label="Saltar tour de bienvenida">
            Saltar tour
          </button>
        )}
      </div>
    </div>
  );
}
