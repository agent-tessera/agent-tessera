import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
    base: "/app/",
    plugins: [
        TanStackRouterVite({
            routesDirectory: "./src/routes",
            generatedRouteTree: "./src/routeTree.gen.ts",
        }),
        react(),
        tailwindcss(),
    ],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    build: {
        outDir: path.resolve(__dirname, "../server/src/tessera_server/web/dist"),
        emptyOutDir: true,
    },
    server: {
        port: 5173,
        proxy: {
            "/v1": "http://127.0.0.1:8700",
            "/ui": "http://127.0.0.1:8700",
            "/healthz": "http://127.0.0.1:8700",
        },
    },
});
