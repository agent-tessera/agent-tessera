/**
 * Detect L5 permissive policies (literal `*` in model/tool/data allow lists).
 */

const KIND_BLOCKS: ReadonlyArray<{ key: string; label: string }> = [
    { key: "models", label: "model" },
    { key: "tools", label: "tool" },
    { key: "data", label: "data" },
];

/**
 * List policy kinds whose allow list contains a literal `*`.
 *
 * Scans YAML text without a full parser: for each `models` / `tools` /
 * `data` block, look at the nested `allow` list for a bare `*` entry
 * (quoted or unquoted), matching the classic UI / posture board rule.
 *
 * @param policyYaml - Stored agent policy text, or empty when unset.
 * @returns Kind names such as `model`, `tool`, or `data`.
 */
export function permissiveKinds(policyYaml: string): string[] {
    if (!policyYaml.trim()) {
        return [];
    }
    const found: string[] = [];
    for (const { key, label } of KIND_BLOCKS) {
        if (blockAllowsStar(policyYaml, key)) {
            found.push(label);
        }
    }
    return found;
}

/**
 * Whether a `spec.<block>.allow` list contains a literal `*`.
 *
 * @param yaml - Full policy YAML.
 * @param block - `models`, `tools`, or `data`.
 */
function blockAllowsStar(yaml: string, block: string): boolean {
    const lines = yaml.split(/\r?\n/);
    let inBlock = false;
    let blockIndent = 0;
    let inAllow = false;
    let allowIndent = 0;

    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) {
            continue;
        }
        const indent = line.length - line.trimStart().length;

        if (!inBlock) {
            if (trimmed === `${block}:`) {
                inBlock = true;
                blockIndent = indent;
            }
            continue;
        }

        if (indent <= blockIndent) {
            break;
        }

        if (!inAllow) {
            if (trimmed === "allow:") {
                inAllow = true;
                allowIndent = indent;
                continue;
            }
            const inline = /^allow:\s*\[(.*)\]\s*$/.exec(trimmed);
            if (inline) {
                return listHasStar(inline[1] ?? "");
            }
            continue;
        }

        if (indent <= allowIndent) {
            inAllow = false;
            if (indent <= blockIndent) {
                break;
            }
            // Fall through to treat this sibling of allow inside the block.
            if (trimmed === "allow:") {
                inAllow = true;
                allowIndent = indent;
            }
            continue;
        }

        if (trimmed.startsWith("-")) {
            const item = trimmed.replace(/^-\s*/, "").trim();
            if (isStarLiteral(item)) {
                return true;
            }
        }
    }
    return false;
}

/**
 * Whether a YAML scalar is the literal star.
 *
 * @param item - List item text after `- `.
 */
function isStarLiteral(item: string): boolean {
    return item === "*" || item === '"*"' || item === "'*'";
}

/**
 * Whether a flow-style list string contains a star entry.
 *
 * @param inner - Contents between `[` and `]`.
 */
function listHasStar(inner: string): boolean {
    return inner
        .split(",")
        .map((part) => part.trim())
        .some((part) => isStarLiteral(part));
}
