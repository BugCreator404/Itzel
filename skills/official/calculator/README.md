# calculator

> Skill oficial de [Itzel](https://github.com/BugCreator404/itzel) 🦎

## ¿Qué hace? / What does it do?

**ES:** Evalúa expresiones matemáticas de forma segura: aritmética, potencias,
raíces y funciones de `math` (sin, cos, log, sqrt…). El código del usuario
**nunca se ejecuta** — solo se evalúa una whitelist de nodos matemáticos.

**EN:** Safely evaluates math expressions: arithmetic, powers, roots and `math`
functions. No arbitrary code execution — only a whitelist of math AST nodes.

## Uso / Usage

```
calcula 2 + 2 * 10
cuánto es sqrt(144) + pi
calculate (15 * 4) / 3
```

## Funciones disponibles / Available functions

`abs round min max sqrt sin cos tan log log2 log10 exp floor ceil degrees radians`
y las constantes `pi`, `e`, `tau`.

## Permisos / Permissions

Ninguno — puro cómputo local. / None — pure local computation.
