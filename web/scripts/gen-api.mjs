#!/usr/bin/env node
/**
 * Regenerate web/openapi.json and web/src/api/schema.d.ts from the FastAPI app.
 *
 * Runs `uv run python scripts/export_openapi.py` from the repo root, then
 * openapi-typescript against the snapshot.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..");
const openapiJson = path.join(webRoot, "openapi.json");
const schemaOut = path.join(webRoot, "src", "api", "schema.d.ts");

function run(command, args, options = {}) {
    const result = spawnSync(command, args, {
        stdio: "inherit",
        ...options,
    });
    if (result.error) {
        throw result.error;
    }
    if (result.status !== 0) {
        process.exit(result.status ?? 1);
    }
}

run("uv", ["run", "python", "scripts/export_openapi.py", "-o", openapiJson], {
    cwd: repoRoot,
    env: process.env,
});

run(
    path.join(webRoot, "node_modules", ".bin", "openapi-typescript"),
    [openapiJson, "-o", schemaOut],
    { cwd: webRoot, env: process.env },
);

console.log(`wrote ${path.relative(webRoot, schemaOut)}`);
