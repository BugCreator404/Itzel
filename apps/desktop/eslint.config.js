// Configuración plana de ESLint 9 (flat config) para el desktop de Itzel.
// Reemplaza al formato .eslintrc, eliminado por defecto en ESLint v9.
// Solo usa @typescript-eslint/{parser,plugin} (ya instalados) — sin añadir deps.
//
// Flat config for ESLint 9. Lints the TypeScript/React sources under src/.
// Establece una baseline razonable: reglas de alto valor que el código actual
// pasa limpio. No incluye el preset `recommended` completo (type-checked) a
// propósito — se puede endurecer en una sesión futura sin romper el CI hoy.
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  // Artefactos y código que NO linteamos.
  {
    ignores: [
      "dist/**",
      "coverage/**",
      "node_modules/**",
      "src-tauri/**",
      "*.config.*",
      "vite-env.d.ts",
    ],
  },

  // Fuentes TypeScript + React bajo src/.
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooks,
    },
    rules: {
      // TypeScript (tsc --noEmit en el CI) ya verifica símbolos no definidos;
      // apagar no-undef evita falsos positivos con globals del navegador/Tauri.
      "no-undef": "off",

      // Hooks de React: rules-of-hooks atrapa bugs reales (orden/condicional);
      // exhaustive-deps avisa de dependencias faltantes en useEffect/useCallback.
      // El código ya las desactiva puntualmente donde es intencional.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",

      // Calidad básica que el código ya cumple.
      "prefer-const": "error",
      "no-var": "error",
      "no-unused-vars": "off", // lo cubre la versión de @typescript-eslint
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          ignoreRestSiblings: true,
        },
      ],
    },
  },
];
