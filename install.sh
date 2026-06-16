#!/usr/bin/env bash
# install.sh — Instalador universal de Itzel para macOS y Linux
#
# Uso:
#   curl -fsSL https://raw.githubusercontent.com/BugCreator404/itzel/main/install.sh | bash
#   bash install.sh               # instalación estándar
#   ITZEL_VERSION=0.2.0 bash install.sh   # versión específica
#
# Variables de entorno (opcionales):
#   ITZEL_VERSION   Versión a instalar (default: latest)
#   ITZEL_DIR       Directorio de instalación (default: ~/.local/bin en Linux, /usr/local/bin en macOS)
#   ITZEL_NO_PATH   Si está definida, no modifica el PATH
#   ITZEL_NO_SETUP  Si está definida, no lanza `itzel setup` al final

set -euo pipefail

# ─── colores ──────────────────────────────────────────────────────────────────
PINK='\033[38;2;249;168;212m'
TEAL='\033[38;2;78;205;196m'
YELLOW='\033[38;2;251;191;36m'
RED='\033[38;2;248;113;113m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "${TEAL}✓${RESET} $1"; }
err()  { echo -e "${RED}✗ Error:${RESET} $1" >&2; }
warn() { echo -e "${YELLOW}⚠${RESET} $1"; }
info() { echo -e "${DIM}  $1${RESET}"; }
step() { echo -e "\n${BOLD}${PINK}[$1]${RESET} $2"; }

REPO="BugCreator404/itzel"
DATA_DIR="${HOME}/.itzel"

# ─── paso 1: detectar OS y arquitectura ───────────────────────────────────────
step "1/6" "Detectando sistema"

OS="$(uname -s)"
ARCH="$(uname -m)"

case "${OS}" in
  Darwin)
    PLATFORM="macos"
    ASSET_EXT=".dmg"
    DEFAULT_BIN_DIR="/usr/local/bin"
    # macOS universal → siempre usar el .dmg (no hay binario CLI standalone aún)
    # Para el CLI Python usamos pip install
    ;;
  Linux)
    PLATFORM="linux"
    ASSET_EXT=".AppImage"
    DEFAULT_BIN_DIR="${HOME}/.local/bin"
    ;;
  *)
    err "Sistema operativo no soportado: ${OS}"
    err "En Windows usa: install.ps1"
    exit 1
    ;;
esac

ok "Sistema: ${PLATFORM} (${ARCH})"

# ─── paso 2: resolver versión ──────────────────────────────────────────────────
step "2/6" "Obteniendo versión"

ITZEL_VERSION="${ITZEL_VERSION:-}"
if [[ -z "${ITZEL_VERSION}" ]]; then
  if command -v curl &>/dev/null; then
    ITZEL_VERSION=$(curl -fsSL \
      "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep '"tag_name"' | head -1 \
      | sed 's/.*"tag_name": *"v\?\([^"]*\)".*/\1/')
  elif command -v wget &>/dev/null; then
    ITZEL_VERSION=$(wget -qO- \
      "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep '"tag_name"' | head -1 \
      | sed 's/.*"tag_name": *"v\?\([^"]*\)".*/\1/')
  else
    err "Se necesita curl o wget para descargar."
    exit 1
  fi
fi

if [[ -z "${ITZEL_VERSION}" ]]; then
  err "No se pudo resolver la versión. Especifícala con ITZEL_VERSION=x.y.z"
  exit 1
fi

ok "Versión: ${ITZEL_VERSION}"

# ─── paso 3: descargar e instalar el CLI Python ────────────────────────────────
step "3/6" "Instalando itzel-cli"

# El CLI es un paquete Python que se instala con pip
if command -v python3 &>/dev/null; then
  PYTHON="python3"
elif command -v python &>/dev/null; then
  PYTHON="python"
else
  err "Python 3.11+ es requerido. Instálalo y vuelve a ejecutar este script."
  exit 1
fi

PY_VERSION="$("${PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
  ok "Python ${PY_VERSION}"
else
  err "Python ${PY_VERSION} detectado — se requiere 3.11+"
  exit 1
fi

# Instalar el CLI con pipx (aislado) o pip
BIN_DIR="${ITZEL_DIR:-${DEFAULT_BIN_DIR}}"
if command -v pipx &>/dev/null; then
  info "Instalando con pipx (entorno aislado)…"
  pipx install "itzel-cli==${ITZEL_VERSION}" 2>/dev/null \
    || pipx install "git+https://github.com/${REPO}.git@v${ITZEL_VERSION}#subdirectory=apps/cli"
  # pipx gestiona el PATH por sí solo
  BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "${HOME}/.local/bin")"
else
  info "Instalando con pip en el entorno actual…"
  "${PYTHON}" -m pip install --quiet \
    "git+https://github.com/${REPO}.git@v${ITZEL_VERSION}#subdirectory=apps/cli"
fi

ok "itzel-cli instalado"

# ─── paso 4: verificar instalación ────────────────────────────────────────────
step "4/6" "Verificando binario"

ITZEL_BIN="$(command -v itzel 2>/dev/null || echo "")"
if [[ -z "${ITZEL_BIN}" ]]; then
  warn "El comando 'itzel' no está en PATH todavía."
  ITZEL_BIN="${BIN_DIR}/itzel"
fi

if [[ -f "${ITZEL_BIN}" ]]; then
  ok "Binario: ${ITZEL_BIN}"
else
  warn "No se encontró el binario en ${BIN_DIR}"
fi

# ─── paso 5: configurar PATH ───────────────────────────────────────────────────
step "5/6" "Configurando PATH"

if [[ -z "${ITZEL_NO_PATH:-}" ]]; then
  _add_to_path() {
    local dir="$1"
    local shell_file="$2"
    if [[ -f "${shell_file}" ]] && ! grep -q "${dir}" "${shell_file}" 2>/dev/null; then
      echo "" >> "${shell_file}"
      echo "# Itzel" >> "${shell_file}"
      echo "export PATH=\"${dir}:\$PATH\"" >> "${shell_file}"
      info "Añadido a ${shell_file}"
    fi
  }

  _add_to_path "${BIN_DIR}" "${HOME}/.bashrc"
  _add_to_path "${BIN_DIR}" "${HOME}/.zshrc"
  _add_to_path "${BIN_DIR}" "${HOME}/.profile"

  # Aplicar en la sesión actual
  export PATH="${BIN_DIR}:${PATH}"
  ok "PATH actualizado"
else
  info "ITZEL_NO_PATH definida — PATH no modificado"
fi

# ─── paso 6: crear directorio de datos y lanzar setup ─────────────────────────
step "6/6" "Finalizando"

mkdir -p "${DATA_DIR}" "${DATA_DIR}/models" "${DATA_DIR}/logs"
ok "Directorio de datos: ${DATA_DIR}"

echo ""
echo -e "${BOLD}${PINK}🦎 Itzel ${ITZEL_VERSION} instalada${RESET}"
echo ""

if [[ -z "${ITZEL_NO_SETUP:-}" ]]; then
  if command -v itzel &>/dev/null; then
    echo -e "${DIM}Lanzando el wizard de configuración inicial…${RESET}"
    echo ""
    itzel setup
  else
    echo -e "${YELLOW}Para continuar, abre una terminal nueva y ejecuta:${RESET}"
    echo -e "  ${TEAL}itzel setup${RESET}"
    echo ""
  fi
else
  echo -e "${DIM}Para configurar Itzel:${RESET}  ${TEAL}itzel setup${RESET}"
  echo ""
fi
