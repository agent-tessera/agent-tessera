#!/usr/bin/env node
/**
 * Fail when a production or transitive dependency uses a disallowed license.
 *
 * Allow-list: MIT, ISC, BSD, Apache-2.0 (and common
 * SPDX variants). Vendored fonts (OFL) are checked separately under VENDOR.md.
 */
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const checker = require("license-checker");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const ALLOWED = new Set([
    "MIT",
    "ISC",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "Apache 2.0",
    "0BSD",
    "Unlicense",
    "CC0-1.0",
    "BlueOak-1.0.0",
]);

const start = process.env.LICENSE_CHECK_START
    ? path.resolve(process.env.LICENSE_CHECK_START)
    : root;

checker.init(
    {
        start,
        production: true,
        excludePrivatePackages: true,
    },
    (err, packages) => {
        if (err) {
            console.error(err);
            process.exit(1);
        }
        const violations = [];
        for (const [name, info] of Object.entries(packages)) {
            const licenses = String(info.licenses ?? "UNKNOWN")
                .split(/\s+(?:OR|AND)\s+|\/|\s*\|\s*/i)
                .map((part) => part.replace(/[()*]/g, "").trim())
                .filter(Boolean);
            const ok =
                licenses.length > 0 &&
                licenses.every((license) => ALLOWED.has(license));
            if (!ok) {
                violations.push(`${name}: ${info.licenses}`);
            }
        }
        if (violations.length > 0) {
            console.error("Disallowed dependency licenses:");
            for (const line of violations) {
                console.error(`  ${line}`);
            }
            process.exit(1);
        }
        console.log(`Licenses OK (${Object.keys(packages).length} packages).`);
    },
);
