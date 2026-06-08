# Cómo contribuir a Itzel · Contributing to Itzel

---

## Español

### ¡Bienvenido/a! 🦎

Itzel es open source y comunitaria. Cualquier programador puede mejorarla.
Este documento explica cómo contribuir de forma efectiva.

### Formas de contribuir

| Tipo | Cómo |
|------|------|
| 🐛 Bug fix | Abre un Issue → Fork → PR |
| 🧩 Nueva skill | Crea en `skills/community/` → PR |
| 🧠 Dataset | Agrega a `models/datasets/` → PR |
| 🌍 Traducción | Edita `packages/core/itzel_core/i18n/` → PR |
| 📚 Docs | Edita `docs/` → PR |
| 🤖 Agente | Agrega en `packages/agents/community/` → PR |

### Pasos

```bash
# 1. Fork y clonar
git clone https://github.com/TU_USUARIO/itzel.git
cd itzel

# 2. Crear rama
git checkout -b feat/mi-contribucion

# 3. Instalar dependencias
pnpm install
pip install -e packages/core[dev]
pip install -e apps/cli[dev]

# 4. Hacer cambios y tests
pnpm test
pytest packages/core/tests/

# 5. Commit semántico
git commit -m "feat: agrega skill para X"

# 6. Pull Request con descripción clara
```

### Convención de commits

```
feat:     nueva funcionalidad
fix:      corrección de bug
docs:     solo documentación
style:    formato (sin lógica)
refactor: refactoring sin nueva funcionalidad
test:     añadir o corregir tests
chore:    tareas de mantenimiento
```

### Código de conducta

Todos los espacios de Itzel siguen el [Contributor Covenant](CODE_OF_CONDUCT.md).
Sé amable, inclusivo/a y constructivo/a.

### Issues etiquetados

- `good first issue` — perfecto para empezar
- `help wanted` — necesitamos ayuda aquí
- `community skill` — propuesta de skill nueva
- `model improvement` — mejoras al dataset

---

## English

### Welcome! 🦎

Itzel is open source and community-driven. Any developer can improve it.
This document explains how to contribute effectively.

### Ways to contribute

| Type | How |
|------|-----|
| 🐛 Bug fix | Open Issue → Fork → PR |
| 🧩 New skill | Create in `skills/community/` → PR |
| 🧠 Dataset | Add to `models/datasets/` → PR |
| 🌍 Translation | Edit `packages/core/itzel_core/i18n/` → PR |
| 📚 Docs | Edit `docs/` → PR |
| 🤖 Agent | Add to `packages/agents/community/` → PR |

### Steps

```bash
# 1. Fork and clone
git clone https://github.com/YOUR_USER/itzel.git
cd itzel

# 2. Create branch
git checkout -b feat/my-contribution

# 3. Install dependencies
pnpm install
pip install -e packages/core[dev]
pip install -e apps/cli[dev]

# 4. Make changes and run tests
pnpm test
pytest packages/core/tests/

# 5. Semantic commit
git commit -m "feat: add skill for X"

# 6. Pull Request with clear description
```

### Commit convention

```
feat:     new feature
fix:      bug fix
docs:     documentation only
style:    formatting (no logic change)
refactor: refactoring without new features
test:     add or fix tests
chore:    maintenance tasks
```

### Code of conduct

All Itzel spaces follow the [Contributor Covenant](CODE_OF_CONDUCT.md).
Be kind, inclusive and constructive.

---

*Hecho con ❤️ en Tijuana, México 🇲🇽 · Made with ❤️ in Tijuana, Mexico*
