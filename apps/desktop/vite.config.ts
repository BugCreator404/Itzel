import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";
import path from "path";

const isProd = process.env.NODE_ENV === "production";
// ANALYZE=1 pnpm build → genera un reporte visual del bundle en dist/.
const isAnalyze = process.env.ANALYZE === "1";

const plugins: PluginOption[] = [react()];

if (isAnalyze) {
  plugins.push(
    visualizer({
      filename: "dist/bundle-report.html",
      gzipSize: true,
      brotliSize: true,
      open: true,
    }),
  );
}

export default defineConfig({
  plugins,

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // Tauri espera que el dev server esté en este puerto
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },

  build: {
    // Sin sourcemaps en producción (principio de seguridad #7)
    sourcemap: isProd ? false : "inline",
    target: ["es2021", "chrome100", "safari13"],
    minify: isProd ? "esbuild" : false,
    outDir: "dist",
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      // Tree-shaking agresivo en producción.
      treeshake: isProd
        ? { moduleSideEffects: false, propertyReadSideEffects: false }
        : true,
      output: {
        // Chunk splitting: separa los vendors grandes para mejor cacheo.
        manualChunks: {
          react: ["react", "react-dom"],
          tauri: [
            "@tauri-apps/api",
            "@tauri-apps/plugin-shell",
            "@tauri-apps/plugin-notification",
            "@tauri-apps/plugin-global-shortcut",
            "@tauri-apps/plugin-clipboard-manager",
            "@tauri-apps/plugin-updater",
            "@tauri-apps/plugin-process",
          ],
        },
      },
    },
  },
});
