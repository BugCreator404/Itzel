# Datasets de Itzel-1B 🦎

> **ES:** Formato, fuentes y reglas del dataset de entrenamiento.
> **EN:** Format, sources and rules of the training dataset.

El dataset es **abierto y comunitario**. Todos los ejemplos son públicos y se
versionan en este repositorio. Sin datos personales, sin contenido dañino.

---

## Estructura / Structure

```
models/datasets/
├── README.md            # (este archivo)
├── productivity_es/     # Tareas de productividad en español MX
├── productivity_en/     # Tareas de productividad en inglés
├── system_control/      # Comandos del sistema (abrir apps, archivos, etc.)
├── code_python/         # Generación y explicación de código Python
├── conversation/        # Conversación natural ES-MX
└── _prepared/           # GENERADO por prepare_data.py (no editar a mano)
    ├── train.jsonl
    └── val.jsonl
```

Cada categoría contiene uno o más archivos `.jsonl` (un ejemplo por línea).
`_prepared/` se regenera con `python prepare_data.py` — **no lo edites**.

---

## Formato de un ejemplo / Example format

Cada línea es un objeto JSON con este esquema:

```json
{
  "instruction": "Organiza los archivos de Downloads por tipo",
  "input": "",
  "output": "Voy a revisar tu carpeta Downloads y agrupar los archivos por extensión. ¿Quieres que cree subcarpetas como Imágenes, Documentos y Videos? Confírmame antes de mover nada.",
  "language": "es-MX",
  "category": "file_management"
}
```

| Campo | Obligatorio | Descripción |
|---|---|---|
| `instruction` | ✅ | Lo que pide el usuario. |
| `input` | — | Contexto extra (texto, ruta, fragmento). Vacío si no aplica. |
| `output` | ✅ | La respuesta ideal de Itzel. |
| `language` | — | `es-MX` (default) o `en-US`. Decide el system prompt. |
| `category` | — | Subcategoría temática (default: el nombre de la carpeta). |

> Se permiten líneas vacías y comentarios `//` en los `.jsonl`; `prepare_data.py`
> los ignora. Los ejemplos con campos obligatorios vacíos se omiten con aviso.

---

## Reglas de calidad / Quality rules

1. **Español mexicano natural** — nada de traducciones literales del inglés.
2. **Sin PII** — no incluyas nombres reales, correos, teléfonos ni rutas con tu
   usuario real. Usa marcadores como `usuario`, `ejemplo.com`.
3. **Tono de Itzel** — cálido, claro, directo. Útil sin ser verboso.
4. **Confirmar lo irreversible** — si la tarea borra/mueve/sobrescribe, el
   `output` debe pedir confirmación (principio #5).
5. **Privacidad por diseño** — recuerda en los `output` que todo es local cuando
   sea relevante.

---

## Cómo contribuir / How to contribute

```bash
# 1. Añade ejemplos en la categoría adecuada
#    models/datasets/<categoria>/mis_ejemplos.jsonl

# 2. Valida sin escribir nada
python models/itzel-1b/prepare_data.py --dry-run

# 3. Si todo sale OK, abre un Pull Request
```

`--dry-run` reporta cuántos ejemplos se cargaron por categoría y su
distribución por idioma — úsalo para revisar tu aporte antes del PR.

---

## Fuentes / Sources

- **Contribuciones de la comunidad** (PRs revisados por el core team).
- **Ejemplos semilla** escritos por el equipo de Itzel (en cada categoría).
- Datasets públicos con licencia compatible (se documentan aquí al añadirse).

> Todo dato incorporado debe tener licencia compatible con Apache 2.0 y estar
> libre de PII. Cualquier fuente externa se cita explícitamente en este archivo.

---

*Hecho con cariño en México · Made with care in Mexico* 🦎
