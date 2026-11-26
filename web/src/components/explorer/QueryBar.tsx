import { X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { FacetValue } from "@/api/events";
import { Button } from "@/components/ui/button";
import {
    autocompleteDraft,
    explorerChips,
    EXPLORER_TOKEN_KEYS,
    isTokenKey,
    parseQueryInput,
    removeFilterValue,
    setFilterValue,
    type ExplorerSearch,
    type ExplorerTokenKey,
} from "@/lib/explorer-search";
import { cn } from "@/lib/utils";

export type QueryBarProps = {
    search: ExplorerSearch;
    /** Facet values used for token autocomplete. */
    facetValues: Partial<Record<ExplorerTokenKey, FacetValue[]>>;
    onSearchChange: (next: ExplorerSearch) => void;
    className?: string;
};

/**
 * Explorer query bar: chips, free-text resource filter, and token autocomplete.
 */
export function QueryBar({
    search,
    facetValues,
    onSearchChange,
    className,
}: QueryBarProps) {
    const listId = useId();
    const inputRef = useRef<HTMLInputElement>(null);
    const [draft, setDraft] = useState("");
    const [open, setOpen] = useState(false);
    const [highlight, setHighlight] = useState(0);

    const chips = useMemo(() => explorerChips(search), [search]);
    const suggestions = useMemo(() => {
        const active = autocompleteDraft(draft);
        if (!active) {
            if (!draft.trim()) {
                return EXPLORER_TOKEN_KEYS.map((key) => `${key}:`);
            }
            return [];
        }
        const values = facetValues[active.key] ?? [];
        const partial = active.partial.toLowerCase();
        return values
            .map((entry) => entry.value)
            .filter((value) => value.toLowerCase().includes(partial))
            .slice(0, 12)
            .map((value) => `${active.key}:${value}`);
    }, [draft, facetValues]);

    const suggestionKey = suggestions.join("|");
    useEffect(() => {
        setHighlight(0);
    }, [suggestionKey]);

    const commit = (raw: string) => {
        const parsed = parseQueryInput(raw);
        if (parsed.kind === "empty") {
            setDraft("");
            setOpen(false);
            return;
        }
        if (parsed.kind === "resource") {
            onSearchChange(setFilterValue(search, "resource", parsed.value));
        } else {
            onSearchChange(setFilterValue(search, parsed.key, parsed.value));
        }
        setDraft("");
        setOpen(false);
    };

    const applySuggestion = (suggestion: string) => {
        if (suggestion.endsWith(":") && isTokenKey(suggestion.slice(0, -1))) {
            setDraft(suggestion);
            setOpen(true);
            inputRef.current?.focus();
            return;
        }
        commit(suggestion);
    };

    return (
        <div className={cn("space-y-2", className)} data-testid="explorer-query-bar">
            {chips.length > 0 ? (
                <ul
                    className="flex flex-wrap gap-1.5"
                    aria-label="Active filters"
                    data-testid="explorer-chips"
                >
                    {chips.map((chip) => (
                        <li key={`${chip.key}:${chip.value}`}>
                            <button
                                type="button"
                                className="border-border bg-panel text-foreground hover:border-border-strong inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs"
                                onClick={() =>
                                    onSearchChange(
                                        removeFilterValue(search, chip.key, chip.value),
                                    )
                                }
                                aria-label={`Remove filter ${chip.label}`}
                            >
                                <span className="font-mono">{chip.label}</span>
                                <X className="size-3 opacity-70" aria-hidden />
                            </button>
                        </li>
                    ))}
                </ul>
            ) : null}

            <div className="relative">
                <label className="sr-only" htmlFor={listId}>
                    Explorer query
                </label>
                <input
                    ref={inputRef}
                    id={listId}
                    type="text"
                    role="combobox"
                    aria-expanded={open && suggestions.length > 0}
                    aria-controls={`${listId}-listbox`}
                    aria-autocomplete="list"
                    aria-activedescendant={
                        open && suggestions[highlight]
                            ? `${listId}-opt-${highlight}`
                            : undefined
                    }
                    data-testid="explorer-query-input"
                    placeholder="Filter: resource text or agent: name, type:, effect:, …"
                    value={draft}
                    className="border-border bg-panel text-foreground placeholder:text-muted-foreground focus-visible:ring-ring h-10 w-full rounded-md border px-3 text-sm focus-visible:ring-2 focus-visible:outline-none"
                    onChange={(event) => {
                        setDraft(event.target.value);
                        setOpen(true);
                    }}
                    onFocus={() => setOpen(true)}
                    onBlur={() => {
                        window.setTimeout(() => setOpen(false), 120);
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "ArrowDown") {
                            event.preventDefault();
                            setOpen(true);
                            setHighlight((index) =>
                                suggestions.length === 0
                                    ? 0
                                    : (index + 1) % suggestions.length,
                            );
                            return;
                        }
                        if (event.key === "ArrowUp") {
                            event.preventDefault();
                            setHighlight((index) =>
                                suggestions.length === 0
                                    ? 0
                                    : (index - 1 + suggestions.length) %
                                      suggestions.length,
                            );
                            return;
                        }
                        if (event.key === "Escape") {
                            setOpen(false);
                            return;
                        }
                        if (event.key === "Enter") {
                            event.preventDefault();
                            if (open && suggestions[highlight]) {
                                applySuggestion(suggestions[highlight]);
                                return;
                            }
                            commit(draft);
                        }
                    }}
                />
                {open && suggestions.length > 0 ? (
                    <ul
                        id={`${listId}-listbox`}
                        role="listbox"
                        data-testid="explorer-autocomplete"
                        className="border-border bg-panel absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-md border py-1 text-sm"
                    >
                        {suggestions.map((suggestion, index) => (
                            <li
                                key={suggestion}
                                id={`${listId}-opt-${index}`}
                                role="option"
                                aria-selected={index === highlight}
                                className={cn(
                                    "cursor-pointer px-3 py-1.5 font-mono",
                                    index === highlight
                                        ? "bg-muted text-foreground"
                                        : "text-foreground hover:bg-muted",
                                )}
                                onMouseDown={(event) => {
                                    event.preventDefault();
                                    applySuggestion(suggestion);
                                }}
                            >
                                {suggestion}
                            </li>
                        ))}
                    </ul>
                ) : null}
            </div>

            <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                <span>Tokens:</span>
                {EXPLORER_TOKEN_KEYS.map((key) => (
                    <Button
                        key={key}
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 font-mono text-xs"
                        onClick={() => {
                            setDraft(`${key}:`);
                            setOpen(true);
                            inputRef.current?.focus();
                        }}
                    >
                        {key}:
                    </Button>
                ))}
            </div>
        </div>
    );
}
