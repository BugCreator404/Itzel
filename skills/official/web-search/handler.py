"""
Skill oficial: web-search — búsqueda web con DuckDuckGo (sin API key).

Usa el endpoint HTML de DuckDuckGo (html.duckduckgo.com) y parsea los
resultados con regex. Sin API keys, sin tracking de Itzel.

PRIVACIDAD (excepción explícita al principio local-first):
    Esta es la única skill oficial que envía datos fuera de tu máquina —
    exactamente tu consulta de búsqueda, solo a DuckDuckGo y SOLO cuando tú
    la invocas. Nada más sale. Si no la quieres, deshabilítala en
    itzel.config.json → skills.disabled.

English: DuckDuckGo HTML search, no API key. Your query is sent to DuckDuckGo
only when you explicitly invoke this skill — nothing else ever leaves.
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import unquote

_SEARCH_URL  = "https://html.duckduckgo.com/html/"
_TIMEOUT_S   = 8.0
_MAX_RESULTS = 5

#: Un resultado en el HTML de DuckDuckGo: <a class="result__a" href="...">título</a>
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
#: Snippet del resultado: <a class="result__snippet" ...>texto</a>
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

#: DuckDuckGo envuelve las URLs: //duckduckgo.com/l/?uddg=<url-codificada>&...
_UDDG_RE = re.compile(r"uddg=([^&]+)")


def _clean(fragment: str) -> str:
    """Quita tags HTML y decodifica entidades."""
    return html_mod.unescape(_TAG_RE.sub("", fragment)).strip()


def _real_url(href: str) -> str:
    """Extrae la URL real del redirect de DuckDuckGo."""
    match = _UDDG_RE.search(href)
    return unquote(match.group(1)) if match else href


def _extract_query(query: str) -> str:
    """Quita las palabras de trigger: 'busca clima en Tijuana' → 'clima en Tijuana'."""
    return re.sub(
        r"^(busca(?:r)?(?:\s+en\s+la\s+web)?|b[uú]scame|search(?:\s+the\s+web)?(?:\s+for)?)\s*",
        "", query.strip(), flags=re.IGNORECASE,
    ).strip(" ?¿")


def run(query: str, context: dict) -> str:
    """Busca en DuckDuckGo y devuelve los primeros resultados formateados."""
    import httpx

    term = _extract_query(query)
    if not term:
        return "Dime qué buscar, p. ej.: busca recetas de mole poblano"

    try:
        resp = httpx.post(
            _SEARCH_URL,
            data={"q": term},
            timeout=_TIMEOUT_S,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ItzelSearch/1.0)"},
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        return f"La búsqueda de «{term}» tardó demasiado (>{int(_TIMEOUT_S)}s). ¿Tienes internet?"
    except httpx.HTTPError as exc:
        return f"No pude buscar «{term}»: {exc}"

    page = resp.text
    links    = _RESULT_RE.findall(page)
    snippets = [_clean(s) for s in _SNIPPET_RE.findall(page)]

    if not links:
        return f"Sin resultados para «{term}»."

    lines = [f"Resultados para «{term}»:"]
    for i, (href, title_html) in enumerate(links[:_MAX_RESULTS]):
        title   = _clean(title_html)
        url     = _real_url(href)
        snippet = snippets[i] if i < len(snippets) else ""
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        lines.append(f"\n{i + 1}. {title}\n   {url}")
        if snippet:
            lines.append(f"   {snippet}")

    return "\n".join(lines)
