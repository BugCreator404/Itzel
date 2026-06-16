# Formula de Homebrew para Itzel
# Repositorio del tap: github.com/BugCreator404/homebrew-tap
#
# Instalación:
#   brew tap BugCreator404/tap
#   brew install itzel
#
# Actualizar el SHA256 al publicar un release:
#   curl -fsSL https://github.com/BugCreator404/itzel/archive/refs/tags/v<VERSION>.tar.gz \
#     | shasum -a 256
#
# CI: .github/workflows/release.yml puede actualizar esta formula automáticamente
# con `brew bump-formula-pr` o con un PR al tap.

class Itzel < Formula
  desc "IA personal local: ajolote en tu terminal. Local-first, sin telemetría"
  homepage "https://github.com/BugCreator404/itzel"
  # Actualizados en cada release por CI (release.yml → bump tap PR)
  url "https://github.com/BugCreator404/itzel/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_v0.1.0_TARBALL"
  license "MIT"
  head "https://github.com/BugCreator404/itzel.git", branch: "main"

  bottle do
    # Los bottles (binarios precompilados) se generan automáticamente por
    # Homebrew CI cuando se publica en el tap oficial.
    # Por ahora: build from source.
    root_url "https://github.com/BugCreator404/homebrew-tap/releases/download"
  end

  # ─── dependencias ────────────────────────────────────────────────────────────

  depends_on "python@3.12"

  # Deps Python del CLI y core — declaradas aquí para que Homebrew las instale
  # en el virtualenv aislado (sin contaminar el Python del sistema).
  resource "typer" do
    url "https://files.pythonhosted.org/packages/typer-0.12.5.tar.gz"
    sha256 "REPLACE_WITH_SHA256"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/rich-13.9.4.tar.gz"
    sha256 "REPLACE_WITH_SHA256"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/httpx-0.28.1.tar.gz"
    sha256 "REPLACE_WITH_SHA256"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/pydantic-2.10.6.tar.gz"
    sha256 "REPLACE_WITH_SHA256"
  end

  resource "keyring" do
    url "https://files.pythonhosted.org/packages/keyring-25.5.0.tar.gz"
    sha256 "REPLACE_WITH_SHA256"
  end

  resource "sqlcipher3-binary" do
    # Binario precompilado de sqlcipher3 para macOS — evita compilar SQLCipher
    url "https://files.pythonhosted.org/packages/sqlcipher3_binary-0.5.3-cp312-cp312-macosx_12_0_universal2.whl"
    sha256 "REPLACE_WITH_SHA256"
  end

  # ─── instalación ──────────────────────────────────────────────────────────────

  def install
    # Crear un virtualenv aislado con el Python de Homebrew.
    # Todas las dependencias declaradas como `resource` se instalan aquí.
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resources

    # Instalar los paquetes del monorepo en orden de dependencias.
    # `--no-deps` porque ya instalamos las deps como resources.
    venv.pip_install_and_link buildpath/"packages/core", { "extras" => [] }, no_deps: true
    venv.pip_install_and_link buildpath/"apps/cli",     { "extras" => [] }, no_deps: true

    # El entrypoint `itzel` queda en libexec/bin/itzel.
    # Creamos un shim en bin/ que apunta al venv.
    (bin/"itzel").write_env_script(
      libexec/"bin/itzel",
      ITZEL_HOME: "#{var}/itzel",
      # No modificamos PYTHONPATH ni PATH del usuario desde aquí.
    )
  end

  # ─── directorio de datos ──────────────────────────────────────────────────────

  def post_install
    # Crear el directorio de datos del usuario (análogo a ~/.itzel/).
    # `var` en Homebrew = /opt/homebrew/var (compartido entre fórmulas).
    (var/"itzel/models").mkpath
    (var/"itzel/logs").mkpath
  end

  # ─── test ─────────────────────────────────────────────────────────────────────

  test do
    # Verificación rápida: el CLI responde y muestra la versión.
    assert_match version.to_s, shell_output("#{bin}/itzel --version")

    # Smoke test del subcomando status (sin backend — debe fallar con mensaje claro).
    output = shell_output("#{bin}/itzel status 2>&1", 1)
    assert_match "Backend", output
  end

  # ─── caveats ──────────────────────────────────────────────────────────────────

  def caveats
    <<~EOS
      Para configurar Itzel por primera vez:
        itzel setup

      Requisitos del backend de IA:
        - llama.cpp:  incluido (no se necesita nada más)
        - Ollama:     brew install ollama  (opcional, mayor compatibilidad)

      Itzel NO envía datos al exterior. Todo corre localmente.
      Documentacion: https://github.com/BugCreator404/itzel#readme
    EOS
  end
end
