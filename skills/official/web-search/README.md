# web-search

> Skill oficial de [Itzel](https://github.com/BugCreator404/itzel) 🦎

## ¿Qué hace? / What does it do?

**ES:** Busca en la web con DuckDuckGo — **sin API key**, sin registro.
Devuelve los primeros 5 resultados con título, URL y resumen.

**EN:** Searches the web via DuckDuckGo — **no API key**, no signup.
Returns the top 5 results with title, URL and snippet.

## ⚠️ Privacidad / Privacy

Esta es la **única** skill oficial que envía datos fuera de tu máquina:
exactamente tu consulta, solo a DuckDuckGo, y **solo cuando tú la invocas**.
Itzel no añade telemetría ni identificadores.

Si no la quieres, deshabilítala en `itzel.config.json`:

```json
"skills": { "disabled": ["web-search"] }
```

## Uso / Usage

```
busca recetas de mole poblano
búscame el clima en Tijuana
search the web for axolotl conservation
```

## Permisos / Permissions

`network` — declarado en `skill.json` y verificado por el loader.

## Dependencias / Dependencies

Ninguna extra — usa `httpx`, que ya viene con itzel-core.
