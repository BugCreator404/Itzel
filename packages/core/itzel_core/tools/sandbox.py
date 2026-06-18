"""
Sandbox de ejecución de código para el CodeAgent.

Ejecuta código Python o comandos del sistema en un subproceso aislado con:
    - Directorio de trabajo temporal (se borra al terminar).
    - Entorno mínimo y limpio (sin API keys, tokens ni proxies del usuario).
    - Timeout estricto: el proceso se mata si excede el límite.
    - Límite de memoria (RAM) best-effort en POSIX vía resource.setrlimit.

Limitaciones de plataforma (importante para seguridad):
    - El bloqueo de RED no es total: aislarla de verdad requiere namespaces
      (Linux `unshare -n`) o un firewall por proceso. Aquí se limpia el entorno
      de red (proxies) como mitigación, y se documenta. NO ejecutes código
      no confiable esperando que no tenga red en esta versión.
    - El límite de RAM con setrlimit solo aplica en POSIX. En Windows el control
      estricto requiere Job Objects (pywin32) — pendiente; el timeout es la
      defensa principal mientras tanto.

English summary:
    Isolated subprocess runner for the CodeAgent. Temp cwd, clean env, hard
    timeout, best-effort RAM cap on POSIX. Network isolation is NOT guaranteed
    on this version — documented limitation.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# resource solo existe en POSIX (Linux/macOS)
try:
    import resource  # type: ignore
    _HAS_RESOURCE = True
except ImportError:
    resource = None  # type: ignore
    _HAS_RESOURCE = False

_IS_WINDOWS = sys.platform.startswith("win")

# Variables de entorno que sí se conservan en el sandbox.
# Todo lo demás (API keys, tokens, proxies…) se descarta.
_SAFE_ENV_KEYS = {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "HOME", "USERPROFILE",
    "COMSPEC", "PATHEXT",
}


# ─── resultado ────────────────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    """Resultado de una ejecución en el sandbox."""
    stdout:      str
    stderr:      str
    returncode:  int
    timed_out:   bool
    duration_s:  float

    @property
    def ok(self) -> bool:
        """True si terminó sin timeout y con código de salida 0."""
        return not self.timed_out and self.returncode == 0


# ─── runner ───────────────────────────────────────────────────────────────────

class SandboxRunner:
    """
    Ejecuta código en un subproceso con límites de recursos.

    Uso:
        sb = SandboxRunner(timeout=30, ram_mb=512)
        res = sb.run_python("print(2 + 2)")
        print(res.stdout)   # "4\\n"
    """

    def __init__(
        self,
        *,
        timeout:       int = 30,
        ram_mb:        int = 512,
        allow_network: bool = False,
        python_exe:    str | None = None,
    ) -> None:
        self.timeout       = timeout
        self.ram_mb        = ram_mb
        self.allow_network = allow_network
        self.python_exe    = python_exe or sys.executable

    # ── API pública ───────────────────────────────────────────────────────────

    def run_python(
        self,
        code:    str,
        *,
        timeout: int | None = None,
    ) -> SandboxResult:
        """
        Ejecuta `code` como un script de Python aislado.

        El código se escribe en un archivo temporal y se ejecuta con el
        intérprete actual en modo aislado (-I: ignora PYTHON* del entorno
        y el directorio del usuario en sys.path).
        """
        workdir = Path(tempfile.mkdtemp(prefix="itzel-sbx-"))
        script  = workdir / "script.py"
        try:
            script.write_text(code, encoding="utf-8")
            argv = [self.python_exe, "-I", "-B", str(script)]
            return self._run(argv, workdir, timeout)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def run_argv(
        self,
        argv:    list[str],
        *,
        timeout: int | None = None,
        stdin:   str | None = None,
    ) -> SandboxResult:
        """
        Ejecuta una lista de argumentos directamente (sin shell).

        El CodeAgent construye el argv apropiado por OS
        (p. ej. ["bash", "-c", cmd] o ["powershell", "-Command", cmd]).
        """
        workdir = Path(tempfile.mkdtemp(prefix="itzel-sbx-"))
        try:
            return self._run(argv, workdir, timeout, stdin=stdin)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # ── internals ─────────────────────────────────────────────────────────────

    def _run(
        self,
        argv:    list[str],
        workdir: Path,
        timeout: int | None,
        stdin:   str | None = None,
    ) -> SandboxResult:
        """
        Ejecuta argv con límites; nunca lanza — devuelve SandboxResult.

        Usa Popen (no subprocess.run) para poder matar el ÁRBOL completo de
        procesos en timeout. Si solo matáramos el proceso directo, un hijo
        (p. ej. 'sleep' lanzado por el shell) quedaría huérfano manteniendo
        abiertos los pipes y bloqueando la lectura hasta que terminara solo.
        """
        limit  = timeout if timeout is not None else self.timeout
        env    = self._clean_env(workdir)
        kwargs = self._platform_kwargs()

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd    = str(workdir),
                env    = env,
                stdin  = subprocess.PIPE if stdin is not None else None,
                stdout = subprocess.PIPE,
                stderr = subprocess.PIPE,
                text   = True,
                **kwargs,
            )
        except FileNotFoundError as exc:
            return SandboxResult(
                stdout="", stderr=f"Ejecutable no encontrado: {exc}",
                returncode=127, timed_out=False,
                duration_s=round(time.monotonic() - start, 3),
            )
        except Exception as exc:  # noqa: BLE001 — el sandbox nunca debe propagar
            return SandboxResult(
                stdout="", stderr=f"Error en el sandbox: {exc}",
                returncode=-1, timed_out=False,
                duration_s=round(time.monotonic() - start, 3),
            )

        try:
            out, err = proc.communicate(input=stdin, timeout=limit)
            return SandboxResult(
                stdout     = out or "",
                stderr     = err or "",
                returncode = proc.returncode,
                timed_out  = False,
                duration_s = round(time.monotonic() - start, 3),
            )
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            # Drenar lo que el proceso alcanzó a emitir (sin volver a colgarse).
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            return SandboxResult(
                stdout     = out or "",
                stderr     = err or "",
                returncode = -1,
                timed_out  = True,
                duration_s = round(time.monotonic() - start, 3),
            )
        except Exception as exc:  # noqa: BLE001
            self._kill_tree(proc)
            return SandboxResult(
                stdout="", stderr=f"Error en el sandbox: {exc}",
                returncode=-1, timed_out=False,
                duration_s=round(time.monotonic() - start, 3),
            )

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        """
        Mata el proceso y todos sus descendientes.

        - Windows: taskkill /T mata el árbol por PID.
        - POSIX:   killpg sobre el grupo (el hijo es líder por os.setsid).
        """
        if proc.poll() is not None:
            return
        if _IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _clean_env(self, workdir: Path) -> dict[str, str]:
        """Construye un entorno mínimo y seguro para el subproceso."""
        env = {
            k: v for k, v in os.environ.items()
            if k in _SAFE_ENV_KEYS
        }
        # Forzar comportamiento determinista y UTF-8
        env["PYTHONIOENCODING"]     = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["TEMP"] = env["TMP"] = str(workdir)
        # Mitigación de red: sin proxies heredados (no garantiza aislamiento).
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                      "http_proxy", "https_proxy", "all_proxy"):
            env.pop(proxy, None)
        return env

    def _platform_kwargs(self) -> dict:
        """kwargs específicos de plataforma para subprocess.run."""
        kwargs: dict = {}
        if _IS_WINDOWS:
            # No abrir ventana de consola al lanzar el subproceso.
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        elif _HAS_RESOURCE:
            # POSIX: aplicar límites de recursos en el hijo antes de exec.
            kwargs["preexec_fn"] = self._apply_limits
        return kwargs

    def _apply_limits(self) -> None:
        """
        Aplica límites de recursos en el proceso hijo (solo POSIX).
        Se ejecuta entre fork() y exec() — debe ser mínimo y sin locks.
        """
        if not _HAS_RESOURCE:
            return
        # Límite de memoria virtual (RAM). Margen extra para el runtime de Python.
        ram_bytes = self.ram_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (ram_bytes, ram_bytes))
        except (ValueError, OSError):
            pass
        # Límite de CPU como red de seguridad además del timeout de pared.
        try:
            cpu_s = max(1, self.timeout)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        except (ValueError, OSError):
            pass
        # Nuevo grupo de procesos: facilita matar todo el árbol en timeout.
        try:
            os.setsid()
        except OSError:
            pass
