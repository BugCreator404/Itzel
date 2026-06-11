# itzel-agents 🦎

Agentes de **Itzel** para operar la computadora del usuario — 100% local, con
confirmación de acciones irreversibles, sandbox para código y auditoría completa.

Cada agente agrupa un conjunto de _tools_ que el modelo **Itzel-1B** puede
invocar (tool calling). Ninguna acción ni dato sale de tu máquina.

---

## Agentes y tools

| Agente          | Tools                                                                                    |
|-----------------|------------------------------------------------------------------------------------------|
| **FileAgent**     | `read_file`, `write_file`, `move_file`, `copy_file`, `delete_file`, `list_dir`, `get_info`, `search_files` |
| **SystemAgent**   | `open_app`, `close_app`, `get_clipboard`, `set_clipboard`, `send_notification`, `get_system_info` |
| **CodeAgent**     | `run_python`, `run_bash`                                                                  |
| **DocumentAgent** | `read_pdf`, `read_docx`, `read_xlsx`, `write_docx`, `summarize`                           |

**21 tools** en total.

---

## Instalación (monorepo)

`itzel-agents` depende de `itzel-core` (que aporta el _tool system_). En
desarrollo, instala primero el core:

```bash
# 1. El core (tool system, config, i18n, logging)
pip install -e packages/core

# 2. Los agentes — elige el alcance:
pip install -e packages/agents              # File, System (parcial), Code
pip install -e packages/agents[documents]   # + PDF / DOCX / XLSX
pip install -e packages/agents[notify]      # + notificaciones nativas (plyer)
pip install -e packages/agents[full]        # todo
```

### Dependencias opcionales

| Extra        | Paquetes                                   | Habilita                          |
|--------------|--------------------------------------------|-----------------------------------|
| (base)       | `psutil`, `pyperclip`                       | sysinfo, portapapeles             |
| `documents`  | `pdfminer.six`, `python-docx`, `openpyxl`   | lectura/escritura de documentos   |
| `notify`     | `plyer`                                      | notificaciones nativas del OS     |

Si falta una dependencia, la tool devuelve un **error legible** con la
instrucción de instalación — nunca rompe el agente.

---

## Uso

```python
from itzel_agents import create_all_agents
from itzel_core.tools import TerminalConfirmHandler

# Inyecta el handler de confirmación (aquí, la terminal en español MX)
agents = create_all_agents(
    confirm_handler=TerminalConfirmHandler("es-MX"),
)

# Esquemas de TODAS las tools para entregar al LLM (tool calling):
schemas = [s for a in agents.values() for s in a.schemas()]

# Ejecutar una tool:
result = agents["file"].invoke("read_file", {"path": "~/notas.txt"})
if result.ok:
    print(result.value)
```

---

## Seguridad

Itzel respeta principios **no negociables** al tocar tu sistema:

1. **Confirmación de acciones irreversibles.** `delete_file`, `close_app`,
   `run_python` y `run_bash` siempre piden confirmación. `move_file`,
   `copy_file` y `write_file` confirman solo si van a **sobrescribir** algo.
   - **Fail-safe:** sin un `ConfirmHandler`, las acciones irreversibles se
     **rechazan** automáticamente.

2. **Sandbox para código.** `run_python` / `run_bash` se ejecutan en un
   subproceso aislado: entorno limpio (sin tus API keys ni proxies), `cwd`
   temporal, _timeout_ estricto que mata todo el árbol de procesos, y límite
   de RAM (best-effort en POSIX).
   > ⚠️ El aislamiento de **red** no es total en esta versión — ver
   > `itzel_core/tools/sandbox.py`. No ejecutes código no confiable esperando
   > que no tenga acceso a red.

3. **Auditoría local.** Cada acción se registra en
   `~/.itzel/logs/actions.log` como línea JSON:
   ```
   timestamp | agent | action | target | result
   ```
   Consulta el historial con `ActionLogger().read_history()`.

4. **Sin telemetría.** Los logs nunca salen de tu equipo.

---

## Integración del handler de confirmación

| Entorno         | Handler                    | Comportamiento                                  |
|-----------------|----------------------------|-------------------------------------------------|
| CLI             | `TerminalConfirmHandler`   | Pregunta `s/n` en la terminal, _timeout_ 30 s    |
| UI (Tauri)      | `CallbackConfirmHandler`   | Dispara el modal con la mascota en modo "think"  |
| Tests / batch   | `AutoConfirmHandler` / `AutoDenyHandler` | Aceptan / rechazan sin interacción |

El texto de confirmación es **bilingüe** (ES-MX / EN-US) según el idioma
configurado, reutilizando el i18n de `itzel-core`.

---

## Tests

```bash
cd packages/agents
pytest                 # todos los tests de agentes
```

Los tests que requieren dependencias opcionales se **saltan** automáticamente
(`skipif`) si no están instaladas. Ningún test toca recursos reales del
usuario: todo ocurre en directorios temporales con un log de auditoría aislado.

---

**Itzel** — _rocío del cielo_ · Hecho en Tijuana, México 🇲🇽 · MIT
