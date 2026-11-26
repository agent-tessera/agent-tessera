import type { EventFacets, FacetValue } from "@/api/events";
import {
    EXPLORER_FACET_KEYS,
    splitCsv,
    toggleFacetValue,
    type ExplorerFacetKey,
    type ExplorerSearch,
} from "@/lib/explorer-search";
import { cn } from "@/lib/utils";

export type FacetSidebarProps = {
    search: ExplorerSearch;
    facets: EventFacets["facets"] | undefined;
    loading?: boolean;
    onSearchChange: (next: ExplorerSearch) => void;
    className?: string;
};

const LABELS: Record<ExplorerFacetKey, string> = {
    agent: "Agent",
    type: "Type",
    effect: "Effect",
    status: "Status",
    kind: "Kind",
};

function isSelected(
    search: ExplorerSearch,
    key: ExplorerFacetKey,
    value: string,
): boolean {
    if (key === "agent" || key === "type" || key === "effect") {
        return splitCsv(search[key]).includes(value);
    }
    return search[key] === value;
}

/**
 * Facet sidebar with top-20 counts from `GET /v1/events/facets`.
 */
export function FacetSidebar({
    search,
    facets,
    loading = false,
    onSearchChange,
    className,
}: FacetSidebarProps) {
    return (
        <aside
            className={cn("space-y-5", className)}
            data-testid="explorer-facets"
            aria-label="Event facets"
        >
            {EXPLORER_FACET_KEYS.map((key) => {
                const values: FacetValue[] = facets?.[key] ?? [];
                return (
                    <section key={key} data-testid={`explorer-facet-${key}`}>
                        <h2 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
                            {LABELS[key]}
                        </h2>
                        {loading && values.length === 0 ? (
                            <p className="text-muted-foreground text-xs">Loading…</p>
                        ) : values.length === 0 ? (
                            <p className="text-muted-foreground text-xs">No values</p>
                        ) : (
                            <ul className="space-y-1">
                                {values.map((entry) => {
                                    const selected = isSelected(
                                        search,
                                        key,
                                        entry.value,
                                    );
                                    return (
                                        <li
                                            key={`${key}:${entry.value}:${entry.id ?? ""}`}
                                        >
                                            <button
                                                type="button"
                                                className={cn(
                                                    "hover:bg-muted flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-sm",
                                                    selected &&
                                                        "bg-muted text-foreground font-medium",
                                                )}
                                                aria-pressed={selected}
                                                data-facet-key={key}
                                                data-facet-value={entry.value}
                                                data-facet-count={entry.count}
                                                onClick={() =>
                                                    onSearchChange(
                                                        toggleFacetValue(
                                                            search,
                                                            key,
                                                            entry.value,
                                                        ),
                                                    )
                                                }
                                            >
                                                <span className="truncate">
                                                    {entry.value}
                                                </span>
                                                <span
                                                    className="text-muted-foreground tabular-nums"
                                                    data-testid={`facet-count-${key}-${entry.value}`}
                                                >
                                                    {entry.count}
                                                </span>
                                            </button>
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                    </section>
                );
            })}
        </aside>
    );
}
