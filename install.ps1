#Requires -Version 5.1
<#
.SYNOPSIS
    Instalador de Itzel para Windows.

.DESCRIPTION
    Descarga e instala el CLI de Itzel desde GitHub Releases.
    Requiere PowerShell 5.1+ (Windows 10+) o PowerShell 7+.

.EXAMPLE
    # Instalación estándar (desde la web):
    irm https://raw.githubusercontent.com/BugCreator404/itzel/main/install.ps1 | iex

    # Version especifica:
    $env:ITZEL_VERSION = "0.2.0"
    .\install.ps1

    # Sin lanzar el setup al final:
    $env:ITZEL_NO_SETUP = "1"
    .\install.ps1

.NOTES
    Variables de entorno opcionales:
        ITZEL_VERSION   Version a instalar (default: latest)
        ITZEL_NO_SETUP  Si esta definida, no lanza itzel setup
        ITZEL_NO_PATH   Si esta definida, no modifica el PATH

    El script instala el CLI Python via pipx (preferido) o pip.
    Las credenciales de MCP van al keychain del OS, nunca en archivos.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── helpers visuales ─────────────────────────────────────────────────────────

function Write-Ok   { param($Msg) Write-Host "  [OK] $Msg" -ForegroundColor Cyan }
function Write-Err  { param($Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red }
function Write-Warn { param($Msg) Write-Host "  [AVISO] $Msg" -ForegroundColor Yellow }
function Write-Info { param($Msg) Write-Host "        $Msg" -ForegroundColor DarkGray }
function Write-Step {
    param([string]$Num, [string]$Title)
    Write-Host ""
    Write-Host "  [$Num] $Title" -ForegroundColor Magenta -NoNewline
    Write-Host ""
    Write-Host "  " + ("-" * 40) -ForegroundColor DarkGray
}

$REPO     = "BugCreator404/itzel"
$DATA_DIR = Join-Path $env:USERPROFILE ".itzel"

# ─── paso 1: detectar OS ──────────────────────────────────────────────────────
Write-Step "1/6" "Detectando sistema"

$arch = if ([System.Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$isArm = $env:PROCESSOR_ARCHITECTURE -eq "ARM64"
if ($isArm) { $arch = "arm64" }

$winVer = [System.Environment]::OSVersion.Version
Write-Ok "Windows $($winVer.Major).$($winVer.Minor) ($arch)"

if ($winVer.Major -lt 10) {
    Write-Err "Se requiere Windows 10 o superior."
    exit 1
}

# ─── paso 2: verificar Python ─────────────────────────────────────────────────
Write-Step "2/6" "Verificando Python"

$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $python = $cmd
                Write-Ok "Python $major.$minor encontrado ($cmd)"
                break
            } else {
                Write-Warn "Python $major.$minor detectado — se requiere 3.11+"
            }
        }
    } catch { continue }
}

if (-not $python) {
    Write-Err "Python 3.11+ no encontrado."
    Write-Host ""
    Write-Host "  Descargalo desde: https://www.python.org/downloads/" -ForegroundColor DarkGray
    Write-Host "  O instala con winget:" -ForegroundColor DarkGray
    Write-Host "    winget install Python.Python.3.13" -ForegroundColor Cyan
    exit 1
}

# ─── paso 3: resolver version ─────────────────────────────────────────────────
Write-Step "3/6" "Obteniendo version"

$itzelVersion = $env:ITZEL_VERSION
if (-not $itzelVersion) {
    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/$REPO/releases/latest" `
            -Headers @{ "User-Agent" = "ItzelInstaller/1.0" } `
            -TimeoutSec 15
        $itzelVersion = $release.tag_name -replace "^v", ""
    } catch {
        Write-Err "No se pudo obtener la ultima version: $_"
        Write-Host "  Especifica la version con: `$env:ITZEL_VERSION = '0.1.0'" -ForegroundColor DarkGray
        exit 1
    }
}

Write-Ok "Version: $itzelVersion"

# ─── paso 4: instalar CLI ─────────────────────────────────────────────────────
Write-Step "4/6" "Instalando itzel-cli"

$pipxAvailable = $null -ne (Get-Command "pipx" -ErrorAction SilentlyContinue)
$installDir    = $null

if ($pipxAvailable) {
    Write-Info "Instalando con pipx (entorno aislado, recomendado)..."
    try {
        $pipxArgs = @("install", "git+https://github.com/$REPO.git@v$itzelVersion#subdirectory=apps/cli")
        & pipx @pipxArgs
        $installDir = & pipx environment --value PIPX_BIN_DIR 2>$null
        if (-not $installDir) {
            $installDir = Join-Path $env:USERPROFILE ".local\bin"
        }
        Write-Ok "Instalado con pipx"
    } catch {
        Write-Warn "pipx fallo: $_"
        Write-Info "Intentando con pip..."
        $pipxAvailable = $false
    }
}

if (-not $pipxAvailable) {
    Write-Info "Instalando con pip en el entorno actual..."
    try {
        & $python -m pip install --quiet `
            "git+https://github.com/$REPO.git@v$itzelVersion#subdirectory=apps/cli"

        # Localizar el directorio Scripts de Python
        $scriptsDir = & $python -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
        $installDir = if ($scriptsDir) { $scriptsDir } else {
            Join-Path ([System.IO.Path]::GetDirectoryName((& $python -c "import sys; print(sys.executable)"))) "Scripts"
        }
        Write-Ok "Instalado con pip"
    } catch {
        Write-Err "Error al instalar: $_"
        exit 1
    }
}

# ─── paso 5: configurar PATH ──────────────────────────────────────────────────
Write-Step "5/6" "Configurando PATH"

if (-not $env:ITZEL_NO_PATH -and $installDir -and (Test-Path $installDir)) {
    $currentPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -notlike "*$installDir*") {
        try {
            [System.Environment]::SetEnvironmentVariable(
                "PATH",
                "$installDir;$currentPath",
                "User"
            )
            # Aplicar en la sesion actual
            $env:PATH = "$installDir;$env:PATH"
            Write-Ok "PATH actualizado (sesion + registro de usuario)"
            Write-Info "Abre una terminal nueva si 'itzel' no responde."
        } catch {
            Write-Warn "No se pudo modificar el PATH del registro: $_"
            Write-Info "Agrega manualmente: $installDir"
        }
    } else {
        Write-Ok "$installDir ya esta en PATH"
    }
} elseif ($env:ITZEL_NO_PATH) {
    Write-Info "ITZEL_NO_PATH definida — PATH no modificado"
}

# ─── paso 6: directorio de datos y setup ──────────────────────────────────────
Write-Step "6/6" "Finalizando"

$dirsToCreate = @(
    $DATA_DIR,
    (Join-Path $DATA_DIR "models"),
    (Join-Path $DATA_DIR "logs"),
    (Join-Path $DATA_DIR "skills")
)
foreach ($dir in $dirsToCreate) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directorio de datos: $DATA_DIR"

Write-Host ""
Write-Host "  Itzel $itzelVersion instalada" -ForegroundColor Magenta
Write-Host ""

if (-not $env:ITZEL_NO_SETUP) {
    $itzelCmd = Get-Command "itzel" -ErrorAction SilentlyContinue
    if ($itzelCmd) {
        Write-Host "  Iniciando el wizard de configuracion inicial..." -ForegroundColor DarkGray
        Write-Host ""
        & itzel setup
    } else {
        Write-Warn "El comando 'itzel' no esta en PATH en esta sesion."
        Write-Host ""
        Write-Host "  Abre una terminal nueva y ejecuta:" -ForegroundColor DarkGray
        Write-Host "    itzel setup" -ForegroundColor Cyan
        Write-Host ""
    }
} else {
    Write-Host "  Para configurar Itzel ejecuta:" -ForegroundColor DarkGray
    Write-Host "    itzel setup" -ForegroundColor Cyan
    Write-Host ""
}
