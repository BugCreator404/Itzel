# Instalación de Itzel / Itzel Installation Guide

> **ES:** Guía completa de instalación para Windows, macOS y Linux.  
> **EN:** Complete installation guide for Windows, macOS and Linux.

---

## Contenido / Table of Contents

- [Requisitos / Requirements](#requisitos--requirements)
- [Instalación rápida / Quick Install](#instalación-rápida--quick-install)
- [Instalación por OS / OS-specific Install](#instalación-por-os--os-specific-install)
- [Primer arranque / First Run](#primer-arranque--first-run)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Desinstalar / Uninstall](#desinstalar--uninstall)

---

## Requisitos / Requirements

| | Windows | macOS | Linux |
|---|---|---|---|
| OS | Windows 10 (22H2+) | macOS 12 Monterey+ | Ubuntu 20.04+ / Arch / Fedora |
| CPU | x64 o ARM64 | Apple Silicon o Intel | x64 |
| RAM | 4 GB mín · 8 GB recomendado | 4 GB mín · 8 GB recomendado | 4 GB mín · 8 GB recomendado |
| Disco | 2 GB libres (+ ~1 GB por modelo de voz) | 2 GB libres | 2 GB libres |
| Python | 3.11+ ([python.org](https://python.org/downloads)) | Incluido en macOS / Homebrew | `python3` del sistema |
| GPU | Opcional — acelera la inferencia | Opcional (MPS soportado) | Opcional (CUDA / ROCm) |

> **Itzel funciona completamente sin GPU.** El modelo Itzel-1B corre en CPU en cualquier equipo moderno.

---

## Instalación rápida / Quick Install

### Un comando / One command

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/BugCreator404/itzel/main/install.sh | bash
```

**Windows (PowerShell 5.1+):**

```powershell
irm https://raw.githubusercontent.com/BugCreator404/itzel/main/install.ps1 | iex
```

Ambos scripts:
1. Detectan el OS y la arquitectura
2. Instalan el CLI con `pipx` (entorno aislado) o `pip`
3. Configuran el PATH
4. Crean `~/.itzel/`
5. Lanzan `itzel setup`

---

## Instalación por OS / OS-specific Install

### Windows

**Opción A — WinGet (recomendado)**

```powershell
winget install BugCreator404.itzel
```

**Opción B — PowerShell (one-liner)**

```powershell
irm https://raw.githubusercontent.com/BugCreator404/itzel/main/install.ps1 | iex
```

**Opción C — Instalador .msi**

Descarga el instalador desde [GitHub Releases](https://github.com/BugCreator404/itzel/releases/latest):

```
itzel_x.y.z_x64_en-US.msi    # Windows x64
itzel_x.y.z_arm64_en-US.msi  # Windows ARM64
```

---

### macOS

**Opción A — Homebrew (recomendado)**

```bash
brew tap BugCreator404/tap
brew install itzel
```

**Opción B — Script de instalación**

```bash
curl -fsSL https://raw.githubusercontent.com/BugCreator404/itzel/main/install.sh | bash
```

**Opción C — .dmg**

Descarga `itzel_x.y.z_universal.dmg` desde [GitHub Releases](https://github.com/BugCreator404/itzel/releases/latest), abre el `.dmg` y arrastra `Itzel.app` a `/Applications`.

> **Nota Gatekeeper:** En macOS puedes ver "app de desarrollador no identificado".  
> Solución: clic derecho → Abrir → Abrir de todas formas.  
> O en terminal: `xattr -d com.apple.quarantine /Applications/Itzel.app`

---

### Linux

**Opción A — Script de instalación**

```bash
curl -fsSL https://raw.githubusercontent.com/BugCreator404/itzel/main/install.sh | bash
```

**Opción B — AppImage**

```bash
# Descargar
wget https://github.com/BugCreator404/itzel/releases/latest/download/itzel_x.y.z_amd64.AppImage

# Dar permisos y ejecutar
chmod +x itzel_x.y.z_amd64.AppImage
./itzel_x.y.z_amd64.AppImage
```

**Opción C — .deb (Ubuntu/Debian)**

```bash
wget https://github.com/BugCreator404/itzel/releases/latest/download/itzel_x.y.z_amd64.deb
sudo dpkg -i itzel_x.y.z_amd64.deb
```

> En Arch Linux: disponible en AUR como `itzel-bin` (mantenido por la comunidad).

---

### Instalación manual (todos los OS)

```bash
# 1. Clonar el repositorio
git clone https://github.com/BugCreator404/itzel.git
cd itzel

# 2. Instalar el backend Python
pip install -e packages/core[dev]

# 3. Instalar el CLI
pip install -e apps/cli

# 4. Instalar dependencias del frontend
pnpm install

# 5. Configurar
itzel setup
```

---

## Primer arranque / First Run

Después de instalar, ejecuta:

```bash
itzel setup
```

El wizard de configuración te guía en 7 pasos:

| Paso | Acción |
|------|--------|
| 1 | Verifica Python, crea `~/.itzel/` |
| 2 | Elige idioma (ES-MX o EN-US) |
| 3 | Elige backend (Ollama detectado / llama.cpp / omitir) |
| 4 | Descarga el modelo con barra de progreso |
| 5 | Configura el hotkey global (default: `Ctrl+Space`) |
| 6 | Pregunta si habilitar voz (descarga Whisper + Kokoro si dices sí) |
| 7 | Genera `~/.itzel/itzel.config.json` y abre la app |

La **app de escritorio** también muestra un tour de 3 pasos la primera vez que se abre.

---

## Troubleshooting

### "Python no encontrado" / "Python not found"

**Windows:** Instala Python 3.11+ desde [python.org](https://python.org/downloads).  
Asegúrate de marcar ✅ **"Add Python to PATH"** durante la instalación.

```powershell
# Verificar:
python --version   # debe mostrar 3.11+
py --version       # alternativa en Windows
```

**macOS:**

```bash
# Con Homebrew:
brew install python@3.12

# Verificar:
python3 --version
```

**Linux (Ubuntu/Debian):**

```bash
sudo apt update && sudo apt install python3.11 python3.11-venv python3-pip
```

---

### "'itzel' no se reconoce como comando" / "'itzel' is not recognized"

El binario no está en el PATH. Soluciones:

**Con pipx (recomendado):**

```bash
pipx install git+https://github.com/BugCreator404/itzel.git#subdirectory=apps/cli
pipx ensurepath
# Reinicia la terminal
```

**Agregar manualmente al PATH:**

```bash
# Linux/macOS — agregar a ~/.bashrc o ~/.zshrc:
export PATH="$HOME/.local/bin:$PATH"

# Windows PowerShell:
$env:PATH += ";$env:USERPROFILE\.local\bin"
```

---

### "Backend offline" al ejecutar `itzel status`

El backend FastAPI no está corriendo. Esto es normal si no lo iniciaste explícitamente.

```bash
# Iniciar el backend:
uvicorn itzel_core.engine:app --host 127.0.0.1 --port 7432

# O con el CLI:
itzel run
```

> La app de escritorio inicia el backend automáticamente. Si usas solo el CLI, necesitas iniciarlo por separado.

---

### "No pude conectar con el backend" (macOS)

macOS puede bloquear conexiones a `localhost` en aplicaciones sin firma.  
**Solución:** En Configuración → Privacidad y Seguridad → Firewall → desactiva el bloqueo para Itzel, o [firma la app](SIGNING.md).

---

### Error al instalar `sqlcipher3` en Linux

```bash
# Instalar la librería de desarrollo:
sudo apt-get install libsqlcipher-dev

# Luego reinstalar:
pip install sqlcipher3
```

---

### "No hay modelos descargados"

```bash
# Descargar Itzel-1B (~900 MB):
itzel model pull itzel-1b

# O con Ollama:
ollama pull llama3.2:3b
```

---

## FAQ

**¿Itzel envía mis datos a internet?**  
No. Todo corre localmente. La única excepción es si usas la skill `web-search` (DuckDuckGo) o conectas un servidor MCP externo — ambas son acciones explícitas del usuario. Telemetría: desactivada siempre.

**¿Funciona sin conexión a internet?**  
Sí. El modelo de IA, la voz y todas las skills oficiales (excepto `web-search`) funcionan sin internet. Solo la descarga inicial del modelo requiere conexión.

**¿Puedo usar mi propio modelo?**  
Sí. Itzel soporta cualquier modelo GGUF compatible con llama.cpp, o cualquier modelo de Ollama.

```bash
# Usar un modelo local:
itzel model use /ruta/a/mi-modelo.gguf

# Usar Ollama:
itzel model use ollama/mistral:7b
```

**¿Cómo actualizo Itzel?**

```bash
itzel update          # actualiza el CLI
# La app de escritorio se actualiza automáticamente (Tauri updater)
```

Con Homebrew:

```bash
brew upgrade itzel
```

Con WinGet:

```bash
winget upgrade BugCreator404.itzel
```

**¿Dónde se guarda mi configuración y mis datos?**

```
~/.itzel/
├── itzel.config.json   # configuración
├── itzel.db            # historial de conversaciones (cifrado AES-256)
├── models/             # modelos descargados
├── logs/               # logs del backend
└── skills/             # skills de usuario
```

---

## Desinstalar / Uninstall

```bash
itzel uninstall
```

El comando pregunta confirmación y elimina:
- El binario del CLI
- La configuración (`~/.itzel/`)
- Los datos locales (historial, modelos descargados)

Las credenciales del keychain del OS se borran automáticamente.

**Con Homebrew:**

```bash
brew uninstall itzel
brew untap BugCreator404/tap   # opcional
```

**Con WinGet:**

```powershell
winget uninstall BugCreator404.itzel
```

**Con pipx:**

```bash
pipx uninstall itzel-cli
```

**Desinstalar solo los datos (conservar el CLI):**

```bash
rm -rf ~/.itzel   # Linux/macOS
# Windows:
Remove-Item -Recurse -Force "$env:USERPROFILE\.itzel"
```

---

*¿Algo no funciona? Abre un [issue en GitHub](https://github.com/BugCreator404/itzel/issues) con tu OS, versión de Python y el mensaje de error completo.*
