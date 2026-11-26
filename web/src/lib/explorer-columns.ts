/** Persist explorer table column visibility in `localStorage`. */

export const EXPLORER_COLUMN_IDS = [
    "time",
    "agent",
    "type",
    "kind",
    "resource",
    "effect",
    "status",
    "duration",
    "rule",
    "run",
] as const;

export type ExplorerColumnId = (typeof EXPLORER_COLUMN_IDS)[number];

export type ExplorerColumnVisibility = Record<ExplorerColumnId, boolean>;

export const EXPLORER_COLUMNS_KEY = "tessera-explorer-columns";

const DEFAULT_VISIBILITY: ExplorerColumnVisibility = {
    time: true,
    agent: true,
    type: true,
    kind: true,
    resource: true,
    effect: true,
    status: true,
    duration: true,
    rule: true,
    run: true,
};

/**
 * Default column visibility (all columns shown).
 */
export function defaultColumnVisibility(): ExplorerColumnVisibility {
    return { ...DEFAULT_VISIBILITY };
}

/**
 * Read column visibility from `localStorage`.
 */
export function readColumnVisibility(): ExplorerColumnVisibility {
    const base = defaultColumnVisibility();
    if (typeof localStorage === "undefined") {
        return base;
    }
    try {
        const raw = localStorage.getItem(EXPLORER_COLUMNS_KEY);
        if (!raw) {
            return base;
        }
        const parsed = JSON.parse(raw) as Partial<Record<string, unknown>>;
        for (const id of EXPLORER_COLUMN_IDS) {
            if (typeof parsed[id] === "boolean") {
                base[id] = parsed[id];
            }
        }
        // Keep at least one column visible.
        if (!EXPLORER_COLUMN_IDS.some((id) => base[id])) {
            base.time = true;
        }
        return base;
    } catch {
        return defaultColumnVisibility();
    }
}

/**
 * Persist column visibility.
 *
 * @param visibility - Column id → visible flag.
 */
export function writeColumnVisibility(visibility: ExplorerColumnVisibility): void {
    if (typeof localStorage === "undefined") {
        return;
    }
    localStorage.setItem(EXPLORER_COLUMNS_KEY, JSON.stringify(visibility));
}
