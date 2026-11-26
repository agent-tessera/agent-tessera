/**
 * Turn `PolicyError` / PyYAML messages into CodeMirror lint diagnostics.
 */

/** One lint marker (1-based line). */
export type PolicyLintDiagnostic = {
    line: number;
    message: string;
};

/**
 * Parse 1-based line numbers from a policy validation error string.
 *
 * PyYAML marks look like `line 5, column 3:`. When no line is present
 * (schema errors), the diagnostic lands on line 1 with the full text.
 *
 * @param error - `PolicyError` / validate `error` body text.
 * @returns Diagnostics for the editor gutter and alert list.
 */
export function policyErrorDiagnostics(error: string): PolicyLintDiagnostic[] {
    const trimmed = error.trim();
    if (!trimmed) {
        return [];
    }
    const lines = new Set<number>();
    for (const match of trimmed.matchAll(/\bline\s+(\d+)\b/gi)) {
        const raw = match[1];
        if (!raw) {
            continue;
        }
        const line = Number.parseInt(raw, 10);
        if (Number.isFinite(line) && line >= 1) {
            lines.add(line);
        }
    }
    if (lines.size === 0) {
        return [{ line: 1, message: trimmed }];
    }
    return [...lines].sort((a, b) => a - b).map((line) => ({ line, message: trimmed }));
}
