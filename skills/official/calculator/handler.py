"""
Skill oficial: calculator — evaluación matemática segura.

Evalúa expresiones aritméticas SIN eval() crudo: se parsea con `ast` y solo se
permiten nodos de una whitelist (números, operadores, funciones de math).
Cualquier otra cosa (nombres arbitrarios, atributos, llamadas no permitidas,
imports…) se rechaza con un error claro.

English: safe math evaluator via ast whitelist — no arbitrary code execution.
"""

from __future__ import annotations

import ast
import math
import operator
import re

# ─── whitelist ────────────────────────────────────────────────────────────────

_BINOPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Funciones y constantes permitidas dentro de la expresión.
_FUNCTIONS: dict[str, object] = {
    "abs":     abs,
    "round":   round,
    "min":     min,
    "max":     max,
    "sqrt":    math.sqrt,
    "sin":     math.sin,
    "cos":     math.cos,
    "tan":     math.tan,
    "log":     math.log,
    "log2":    math.log2,
    "log10":   math.log10,
    "exp":     math.exp,
    "floor":   math.floor,
    "ceil":    math.ceil,
    "degrees": math.degrees,
    "radians": math.radians,
    "pi":      math.pi,
    "e":       math.e,
    "tau":     math.tau,
}

_MAX_EXPR_LEN = 200
_MAX_POW_EXP  = 10_000   # evita 9**9**9 (DoS de CPU/memoria)


# ─── evaluador ────────────────────────────────────────────────────────────────

def _eval_node(node: ast.AST) -> float:
    """Evalúa recursivamente un nodo del AST, solo dentro de la whitelist."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Constante no numérica: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Operador no permitido: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXP:
            raise ValueError(f"Exponente demasiado grande (máx {_MAX_POW_EXP}).")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Operador unario no permitido: {type(node.op).__name__}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("Función no permitida.")
        fn = _FUNCTIONS[node.func.id]
        args = [_eval_node(a) for a in node.args]
        if node.keywords:
            raise ValueError("Argumentos con nombre no permitidos.")
        return fn(*args)  # type: ignore[operator]

    if isinstance(node, ast.Name):
        value = _FUNCTIONS.get(node.id)
        if isinstance(value, (int, float)):
            return value
        raise ValueError(f"Nombre no permitido: '{node.id}'")

    raise ValueError(f"Expresión no permitida: {type(node).__name__}")


def evaluate(expression: str) -> float:
    """Evalúa una expresión matemática de forma segura."""
    if len(expression) > _MAX_EXPR_LEN:
        raise ValueError(f"Expresión demasiado larga (máx {_MAX_EXPR_LEN} chars).")
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


def _extract_expression(query: str) -> str:
    """
    Extrae la parte matemática de una frase natural:
    "calcula 2 + 2" → "2 + 2". Conserva dígitos, operadores, paréntesis,
    nombres de funciones permitidas y comas/puntos.
    """
    # Quitar palabras de trigger comunes al inicio
    cleaned = re.sub(
        r"^(calcula|cu[aá]nto es|calculate|how much is)\s*",
        "", query.strip(), flags=re.IGNORECASE,
    ).rstrip("?¿!¡.")
    return cleaned.strip()


# ─── entrada de la skill ──────────────────────────────────────────────────────

def run(query: str, context: dict) -> str:
    """Evalúa la expresión del usuario y devuelve el resultado formateado."""
    expr = _extract_expression(query)
    if not expr:
        return "Dame una expresión, p. ej.: calcula (15 * 4) + sqrt(81)"

    try:
        result = evaluate(expr)
    except ZeroDivisionError:
        return f"{expr} = ∞ (división entre cero)"
    except (ValueError, SyntaxError) as exc:
        return f"No pude evaluar '{expr}': {exc}"
    except OverflowError:
        return f"El resultado de '{expr}' es demasiado grande."

    # Enteros sin decimales; floats con hasta 10 dígitos significativos
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    formatted = f"{result:,}".replace(",", " ") if isinstance(result, int) else f"{result:.10g}"
    return f"{expr} = {formatted}"
