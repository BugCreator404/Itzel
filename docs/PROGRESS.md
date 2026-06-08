# ITZEL — Log de progreso

## Sesión 1 — Scaffolding completo (2025-06-08)

### ✅ Completado

- [x] Monorepo con pnpm workspaces
- [x] `.gitignore`, `.editorconfig`, `.nvmrc`, `.python-version`
- [x] `apps/desktop/` — Tauri 2 + React 19 + Vite + TypeScript
  - `vite.config.ts` — sin sourcemaps en producción
  - `tauri.conf.json` — ventana 900x650, min 800x600, CSP estricta
  - `Cargo.toml` — plugins: shell, notification, global-shortcut, clipboard
  - `src/main.tsx`, `src/App.tsx`, `src/index.css` — UI base
- [x] `apps/cli/` — CLI Python con Typer
  - Todos los comandos stub: ask, run, chat, voice, setup, status, update, pipe
  - Sub-comandos: model, skills, config, memory, mcp
  - Tests básicos con typer.testing.CliRunner
- [x] `packages/core/` — FastAPI backend
  - `config.py` — carga `itzel.config.json` con pydantic-settings
  - `rate_limiter.py` — token bucket async
  - `router.py` — selección de modelo
  - `memory.py` — SQLite + SQLCipher (memoria episódica)
  - `engine.py` — FastAPI app factory con CORS restrictivo
  - `api/v1/health.py`, `api/v1/chat.py` — endpoints base
  - Tests: config, i18n, rate_limiter, API health
- [x] `packages/core/itzel_core/i18n/` — ES-MX y EN-US completos
- [x] `itzel.config.json` + `itzel.config.schema.json`
- [x] `LICENSE` — MIT con nombre del autor
- [x] `CONTRIBUTING.md` — bilingüe ES-MX + EN
- [x] `.github/workflows/ci.yml` — lint + typecheck + pytest + build Tauri

### 🔜 Próximos pasos (Sesión 2)

1. **Mascota ajolote** — mover el sprite JS del spec a `packages/mascota/src/`
   como módulo exportable y conectarlo a la UI React de Tauri
2. **Integración Ollama** — conectar `packages/core` a llama.cpp o Ollama
   para que `itzel ask` tenga respuestas reales
3. **Shortcuts globales** — implementar los hotkeys en `src-tauri/src/lib.rs`
   usando `tauri-plugin-global-shortcut`

### Estructura actual del repo

```
itzel/
├── apps/
│   ├── desktop/          ✅ Tauri 2 + React 19
│   └── cli/              ✅ Python + Typer
├── packages/
│   ├── core/             ✅ FastAPI + SQLite + i18n
│   ├── voice/            ⏳ Sesión 3
│   ├── mascota/          ⏳ Sesión 2
│   └── agents/           ⏳ Sesión 4+
├── skills/               ✅ Estructura lista
├── models/               ✅ Estructura lista (sin GGUF)
├── docs/                 ✅ Este archivo
├── .github/workflows/    ✅ CI/CD base
├── itzel.config.json     ✅
├── itzel.config.schema.json ✅
├── LICENSE               ✅ MIT
└── CONTRIBUTING.md       ✅ Bilingüe
```
