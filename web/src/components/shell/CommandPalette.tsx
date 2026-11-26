import { useRouter } from "@tanstack/react-router";
import { Search } from "lucide-react";
import {
    useCallback,
    useEffect,
    useId,
    useMemo,
    useRef,
    useState,
    type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { fetchSearch, type SearchResult } from "@/api/search";
import { Kbd } from "@/components/Kbd";
import {
    filterStaticActions,
    TYPE_LABELS,
    type PaletteItem,
} from "@/lib/palette-actions";
import {
    isLikelyRunId,
    isSpanId,
    parseSearchString,
    stripAppHref,
} from "@/lib/palette-ids";
import {
    pushRecentSearch,
    readRecentSearch,
    type RecentSearchItem,
} from "@/lib/recent-search";
import { OPEN_SEARCH_EVENT } from "@/lib/search-events";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 150;

function recentToItems(recent: RecentSearchItem[]): PaletteItem[] {
    return recent.map((item) => ({
        id: `recent:${item.type}:${item.id}`,
        type: "recent",
        title: item.title,
        subtitle: item.subtitle ?? item.type,
        href: item.href,
        group: "Recent",
    }));
}

function resultsToItems(results: SearchResult[]): PaletteItem[] {
    return results.map((item) => ({
        id: `hit:${item.type}:${item.id}`,
        type: item.type,
        title: item.title,
        subtitle: item.subtitle,
        href: item.href,
        group: TYPE_LABELS[item.type] ?? item.type,
    }));
}

function flattenGrouped(items: PaletteItem[]): PaletteItem[] {
    return items;
}

/**
 * Global command palette (⌘K / Ctrl-K / `/` via {@link OPEN_SEARCH_EVENT}).
 *
 * Debounced search, recent items, static actions, and paste-jump for span/run ids.
 */
export function CommandPalette() {
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const [hits, setHits] = useState<SearchResult[]>([]);
    const [recent, setRecent] = useState<RecentSearchItem[]>([]);
    const [active, setActive] = useState(0);
    const [loading, setLoading] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);
    const dialogRef = useRef<HTMLDivElement>(null);
    const listboxId = useId();
    const titleId = useId();

    const close = useCallback(() => {
        setOpen(false);
        setQuery("");
        setHits([]);
        setActive(0);
        setLoading(false);
    }, []);

    const navigateHref = useCallback(
        (href: string) => {
            const { pathname, searchStr } = stripAppHref(href);
            const search = parseSearchString(searchStr);
            // `history.push` skips the `/app` basepath; `navigate` applies it.
            void router.navigate({
                to: pathname,
                search,
            } as never);
        },
        [router],
    );

    const remember = useCallback((item: PaletteItem) => {
        if (!item.href) {
            return;
        }
        const href = item.href.startsWith("/app")
            ? item.href
            : item.href.startsWith("/")
              ? `/app${item.href}`
              : item.href;
        const rawId = item.id.replace(/^hit:|^recent:/, "");
        pushRecentSearch({
            type: item.type === "recent" ? "item" : item.type,
            id: rawId,
            title: item.title,
            subtitle: item.subtitle,
            href,
        });
        setRecent(readRecentSearch());
    }, []);

    const selectItem = useCallback(
        (item: PaletteItem) => {
            item.run?.();
            if (item.href) {
                remember(item);
                navigateHref(
                    item.href.startsWith("/app")
                        ? item.href
                        : item.href.startsWith("/")
                          ? `/app${item.href}`
                          : item.href,
                );
            }
            close();
        },
        [close, navigateHref, remember],
    );

    const jumpPaste = useCallback(
        async (text: string) => {
            const value = text.trim();
            if (isSpanId(value)) {
                setLoading(true);
                try {
                    const response = await fetchSearch(value.toLowerCase(), 8);
                    const span = response.results.find((row) => row.type === "span");
                    if (span) {
                        selectItem({
                            id: `hit:span:${span.id}`,
                            type: "span",
                            title: span.title,
                            subtitle: span.subtitle,
                            href: span.href,
                            group: "Spans",
                        });
                        return;
                    }
                } finally {
                    setLoading(false);
                }
            }
            if (isLikelyRunId(value)) {
                setLoading(true);
                try {
                    const response = await fetchSearch(value, 8);
                    const run = response.results.find(
                        (row) =>
                            row.type === "run" &&
                            row.id.toLowerCase() === value.toLowerCase(),
                    );
                    if (run) {
                        selectItem({
                            id: `hit:run:${run.id}`,
                            type: "run",
                            title: run.title,
                            subtitle: run.subtitle,
                            href: run.href,
                            group: "Runs",
                        });
                        return;
                    }
                    // Exact ULID with no hit yet: still open the trace stub URL.
                    if (/^[0-9A-HJKMNP-TV-Z]{26}$/i.test(value)) {
                        selectItem({
                            id: `hit:run:${value}`,
                            type: "run",
                            title: value,
                            href: `/app/traces/${value}`,
                            group: "Runs",
                        });
                    }
                } finally {
                    setLoading(false);
                }
            }
        },
        [selectItem],
    );

    useEffect(() => {
        const onOpen = () => {
            setRecent(readRecentSearch());
            setOpen(true);
            setQuery("");
            setHits([]);
            setActive(0);
        };
        window.addEventListener(OPEN_SEARCH_EVENT, onOpen);
        return () => window.removeEventListener(OPEN_SEARCH_EVENT, onOpen);
    }, []);

    useEffect(() => {
        if (!open) {
            return;
        }
        const t = window.setTimeout(() => inputRef.current?.focus(), 0);
        return () => window.clearTimeout(t);
    }, [open]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                close();
            }
        };
        window.addEventListener("keydown", onKey, true);
        return () => window.removeEventListener("keydown", onKey, true);
    }, [open, close]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const previouslyFocused = document.activeElement as HTMLElement | null;
        const root = dialogRef.current;
        if (!root) {
            return;
        }
        const focusables = () =>
            Array.from(
                root.querySelectorAll<HTMLElement>(
                    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
                ),
            ).filter((el) => !el.hasAttribute("disabled"));

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Tab") {
                return;
            }
            const nodes = focusables();
            if (nodes.length === 0) {
                return;
            }
            const first = nodes[0];
            const last = nodes[nodes.length - 1];
            if (!first || !last) {
                return;
            }
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        root.addEventListener("keydown", onKeyDown);
        return () => {
            root.removeEventListener("keydown", onKeyDown);
            previouslyFocused?.focus?.();
        };
    }, [open]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const needle = query.trim();
        if (needle.length < 2) {
            setHits([]);
            setLoading(false);
            return;
        }
        let cancelled = false;
        setLoading(true);
        const timer = window.setTimeout(() => {
            void fetchSearch(needle, 8)
                .then((response) => {
                    if (!cancelled) {
                        setHits(response.results);
                    }
                })
                .catch(() => {
                    if (!cancelled) {
                        setHits([]);
                    }
                })
                .finally(() => {
                    if (!cancelled) {
                        setLoading(false);
                    }
                });
        }, DEBOUNCE_MS);
        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [open, query]);

    const items = useMemo(() => {
        const needle = query.trim();
        const actions = filterStaticActions(needle);
        if (!needle) {
            return flattenGrouped([...recentToItems(recent), ...actions]);
        }
        return flattenGrouped([...resultsToItems(hits), ...actions]);
    }, [query, hits, recent]);

    useEffect(() => {
        setActive(0);
    }, [query, hits, recent, open]);

    useEffect(() => {
        const node = listRef.current?.querySelector<HTMLElement>(
            `[data-palette-index="${active}"]`,
        );
        node?.scrollIntoView({ block: "nearest" });
    }, [active, items]);

    const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((index) => (items.length === 0 ? 0 : (index + 1) % items.length));
            return;
        }
        if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((index) =>
                items.length === 0 ? 0 : (index - 1 + items.length) % items.length,
            );
            return;
        }
        if (event.key === "Enter") {
            event.preventDefault();
            const item = items[active];
            if (item) {
                selectItem(item);
            }
        }
    };

    if (!open) {
        return null;
    }

    let lastGroup = "";

    return (
        <div
            className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
            role="presentation"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) {
                    close();
                }
            }}
        >
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="border-border bg-panel flex max-h-[min(28rem,70vh)] w-full max-w-xl flex-col overflow-hidden rounded-lg border shadow-lg"
            >
                <h2 id={titleId} className="sr-only">
                    Command palette
                </h2>
                <div className="border-border flex items-center gap-2 border-b px-3">
                    <Search
                        className="text-muted-foreground size-4 shrink-0"
                        aria-hidden
                    />
                    <input
                        ref={inputRef}
                        type="text"
                        role="combobox"
                        aria-label="Command palette search"
                        aria-expanded="true"
                        aria-controls={listboxId}
                        aria-autocomplete="list"
                        aria-activedescendant={
                            items[active] ? `${listboxId}-option-${active}` : undefined
                        }
                        className="placeholder:text-muted-foreground h-11 w-full bg-transparent text-sm outline-none"
                        placeholder="Search agents, runs, spans…"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        onKeyDown={onInputKeyDown}
                        onPaste={(event) => {
                            const text = (
                                event.clipboardData.getData("text/plain") ||
                                event.clipboardData.getData("text") ||
                                ""
                            ).trim();
                            if (isSpanId(text) || isLikelyRunId(text)) {
                                event.preventDefault();
                                setQuery(text);
                                void jumpPaste(text);
                            }
                        }}
                    />
                    <Kbd>esc</Kbd>
                </div>
                <div
                    ref={listRef}
                    id={listboxId}
                    role="listbox"
                    aria-label="Search results"
                    className="min-h-0 flex-1 overflow-auto p-2"
                >
                    {loading && query.trim().length >= 2 ? (
                        <p className="text-muted-foreground px-2 py-1.5 text-xs">
                            Searching…
                        </p>
                    ) : null}
                    {items.length === 0 ? (
                        <p className="text-muted-foreground px-2 py-6 text-center text-sm">
                            No matches
                        </p>
                    ) : (
                        items.map((item, index) => {
                            const showGroup = item.group !== lastGroup;
                            lastGroup = item.group;
                            return (
                                <div key={item.id}>
                                    {showGroup ? (
                                        <p className="text-muted-foreground px-2 pt-2 pb-1 text-[11px] font-medium tracking-wide uppercase">
                                            {item.group}
                                        </p>
                                    ) : null}
                                    <button
                                        type="button"
                                        id={`${listboxId}-option-${index}`}
                                        role="option"
                                        aria-selected={index === active}
                                        data-palette-index={index}
                                        className={cn(
                                            "flex w-full flex-col items-start rounded-md px-2 py-1.5 text-left text-sm",
                                            index === active
                                                ? "bg-accent-soft text-foreground"
                                                : "hover:bg-muted text-foreground",
                                        )}
                                        onMouseEnter={() => setActive(index)}
                                        onClick={() => selectItem(item)}
                                    >
                                        <span className="font-medium">
                                            {item.title}
                                        </span>
                                        {item.subtitle ? (
                                            <span className="text-muted-foreground font-mono text-xs">
                                                {item.subtitle}
                                            </span>
                                        ) : null}
                                    </button>
                                </div>
                            );
                        })
                    )}
                </div>
                <div className="border-border text-muted-foreground flex items-center gap-3 border-t px-3 py-2 text-xs">
                    <span className="inline-flex items-center gap-1">
                        <Kbd>↑</Kbd>
                        <Kbd>↓</Kbd>
                        navigate
                    </span>
                    <span className="inline-flex items-center gap-1">
                        <Kbd>↵</Kbd>
                        open
                    </span>
                    <span className="inline-flex items-center gap-1">
                        <Kbd>?</Kbd>
                        keyboard map
                    </span>
                </div>
            </div>
        </div>
    );
}
