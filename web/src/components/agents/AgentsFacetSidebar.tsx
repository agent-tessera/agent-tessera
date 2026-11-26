import {
    countAgentFacet,
    splitAgentsCsv,
    toggleAgentsFacet,
    type AgentsFacetKey,
    type AgentsSearch,
} from "@/lib/agents-search";
import { cn } from "@/lib/utils";

export type AgentsFacetSidebarProps = {
    search: AgentsSearch;
    agents: Array<{ status: string; environment: string; autonomy: string }>;
    loading?: boolean;
    onSearchChange: (next: AgentsSearch) => void;
    className?: string;
};

const LABELS: Record<AgentsFacetKey, string> = {
    status: "Status",
    environment: "Environment",
    autonomy: "Autonomy",
};

const FACET_KEYS: AgentsFacetKey[] = ["status", "environment", "autonomy"];

/**
 * Facet sidebar for the agents list (status / environment / autonomy).
 */
export function AgentsFacetSidebar({
    search,
    agents,
    loading = false,
    onSearchChange,
    className,
}: AgentsFacetSidebarProps) {
    return (
        <aside
            className={cn("space-y-5", className)}
            data-testid="agents-facets"
            aria-label="Agent facets"
        >
            {FACET_KEYS.map((key) => {
                const values = countAgentFacet(agents, key);
                const selected = new Set(splitAgentsCsv(search[key]));
                return (
                    <section key={key} data-testid={`agents-facet-${key}`}>
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
                                    const isOn = selected.has(entry.value);
                                    return (
                                        <li key={`${key}:${entry.value}`}>
                                            <button
                                                type="button"
                                                className={cn(
                                                    "hover:bg-muted flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-sm",
                                                    isOn &&
                                                        "bg-muted text-foreground font-medium",
                                                )}
                                                aria-pressed={isOn}
                                                data-facet-key={key}
                                                data-facet-value={entry.value}
                                                data-facet-count={entry.count}
                                                onClick={() => {
                                                    if (key === "environment") {
                                                        // Shell + API take a single environment.
                                                        const next = {
                                                            ...search,
                                                        };
                                                        if (
                                                            search.environment ===
                                                            entry.value
                                                        ) {
                                                            delete next.environment;
                                                        } else {
                                                            next.environment =
                                                                entry.value;
                                                        }
                                                        onSearchChange(next);
                                                        return;
                                                    }
                                                    onSearchChange(
                                                        toggleAgentsFacet(
                                                            search,
                                                            key,
                                                            entry.value,
                                                        ),
                                                    );
                                                }}
                                            >
                                                <span className="truncate capitalize">
                                                    {entry.value}
                                                </span>
                                                <span className="text-muted-foreground tabular-nums">
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
