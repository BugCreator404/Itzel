<div align="center">

# ITZEL

**Tu IA personal local · Open Source · Hecha en México 🇲🇽**

[![License: MIT](https://img.shields.io/badge/Licencia-MIT-f9a8d4?style=for-the-badge&labelColor=1a0828)](LICENSE)
[![Platform](https://img.shields.io/badge/Plataformas-Win%20%7C%20Mac%20%7C%20Linux-c4b5fd?style=for-the-badge&labelColor=1a0828)](https://github.com/BugCreator404/itzel/releases)
[![Model](https://img.shields.io/badge/Modelo-Itzel--1B%20local-6ee7b7?style=for-the-badge&labelColor=1a0828)](models/)
[![PRs Welcome](https://img.shields.io/badge/PRs-bienvenidos-fbbf24?style=for-the-badge&labelColor=1a0828)](CONTRIBUTING.md)

*"Itzel" · Maya · rocío del cielo · la primera inteligencia que ves al despertar*

**[🇲🇽 Español](#español) · [🇺🇸 English](#english)**

</div>

---

## Español

Itzel es una IA personal que vive completamente en tu máquina. Sin internet. Sin suscripciones. Sin telemetría.

Su nombre viene del maya: *"rocío del cielo"*. Su mascota es el **ajolote mexicano** — especie endémica, símbolo de regeneración.

### ✨ ¿Qué hace Itzel?

| Capacidad | Descripción |
|-----------|-------------|
| 🧠 **IA local** | Modelo Itzel-1B corre en tu máquina. Sin internet. Sin suscripciones. |
| 🎤 **Voz** | Whisper STT + Kokoro TTS, 100% local. |
| 🖥️ **Control del sistema** | Archivos, apps, código, documentos. |
| 🦎 **Mascota ajolote** | Sprite pixel art con 6 estados de ánimo. 100% código JS. |
| ⌨️ **CLI completo** | `itzel ask`, `itzel run`, `itzel voice`, y más. |
| ⚡ **Shortcuts** | `Ctrl+Space`, `Ctrl+Shift+V`, `Ctrl+/` y más. |
| 🔒 **Privacidad total** | Sin telemetría. Sin analytics. Tus datos no salen de tu máquina. |

### 🚀 Instalación rápida

```bash
# macOS
brew install BugCreator404/tap/itzel && itzel setup

# Windows
winget install BugCreator404.itzel && itzel setup

# Linux
curl -fsSL https://itzel.ai/install.sh | sh

# Desde código fuente
git clone https://github.com/BugCreator404/itzel.git
cd itzel && pnpm install
pip install -e packages/core && pip install -e apps/cli
itzel setup
```

### ⌨️ Comandos

```bash
itzel ask "¿cómo funciona async/await?"   # pregunta directa
itzel run "organiza mis Downloads"         # agente con plan
itzel chat                                  # chat interactivo
itzel voice                                 # modo de voz
itzel status                               # estado del sistema
cat error.log | itzel pipe "¿qué falla?"  # pipe desde stdin
```

### ⚡ Shortcuts globales

| Atajo | Acción |
|-------|--------|
| `Ctrl + Space` | Abrir / cerrar chat |
| `Ctrl + Shift + V` | Activar voz |
| `Ctrl + Shift + I` | Ventana principal |
| `Ctrl + Shift + S` | Screenshot + preguntar |
| `Ctrl + Shift + C` | Explicar portapapeles |
| `Ctrl + /` | Command palette |

### 🤝 Cómo contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md) para la guía completa.

```
skills/community/     → Agrega tu propia skill
models/datasets/      → Mejora el dataset
packages/mascota/     → Nuevos estados del ajolote
docs/i18n/            → Traduce a más idiomas
```

---

## English

Itzel is a personal AI that runs entirely on your machine. No internet. No subscriptions. No telemetry.

Her name comes from Mayan: *"dew from the sky"*. Her mascot is the **Mexican axolotl** — an endemic species, symbol of regeneration.

### 🚀 Quick install

```bash
# macOS
brew install BugCreator404/tap/itzel && itzel setup

# Windows
winget install BugCreator404.itzel && itzel setup

# Linux
curl -fsSL https://itzel.ai/install.sh | sh
```

### ⌨️ Main commands

```bash
itzel ask "how does async/await work?"   # direct answer
itzel run "organize my Downloads folder" # agent with plan
itzel chat                                # interactive chat
itzel voice                               # voice mode
cat error.log | itzel pipe "what fails?" # stdin pipe
```

### 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. Issues labeled `good first issue` are perfect for getting started.

---

<div align="center">

### Hecho con ❤️ en Tijuana, México 🇲🇽

**Edwin Yair Hernández Limón** · [@BugCreator404](https://github.com/BugCreator404)

*Ing. Sistemas Computacionales · ITT · Full-Stack + IA*

*"El único bug que no se puede parchear es rendirse."* — BugCreator404

</div>
