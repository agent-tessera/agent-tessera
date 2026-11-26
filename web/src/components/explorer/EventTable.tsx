import { Link } from "@tanstack/react-router";
import { createColumnHelper, type VisibilityState } from "@tanstack/react-table";
import { useMemo, useState } from "react";

import type { EventItem } from "@/api/events";
import { DataTable } from "@/components/DataTable";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { StatusBadge, type StatusKind } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import {
    EXPLORER_COLUMN_IDS,
    readColumnVisibility,
    writeColumnVisibility,
    type ExplorerColumnId,
    type ExplorerColumnVisibility,
} from "@/lib/explorer-columns";
import { newEventsLabel } from "@/lib/explorer-live";
import { formatDuration } from "@/lib/overview-metrics";
import { cn } from "@/lib/utils";

export type EventTableProps = {
    items: EventItem[];
    selectedId?: string;
    loading?: boolean;
    loadingMore?: boolean;
    onSelect: (eventId: string) => void;
    onEndReached?: () => void;
    onExport?: () => void;
    /** Ids briefly highlighted after a live prepend. */
    highlightedIds?: ReadonlySet<string>;
    /** Buffered live events while paused. */
    liveBufferedCount?: number;
    onLiveResume?: () => void;
    onViewportScroll?: (scrollTop: number) => void;
    scrollResetToken?: number;
    className?: string;
};

const EFFECTS = new Set<EffectKind>(["allow", "deny", "require_approval"]);
const STATUSES = new Set<StatusKind>(["ok", "error", "revoked", "running"]);
const COLUMN_LABELS: Record<ExplorerColumnId, string> = {
    time: "Time",
    agent: "Agent",
    type: "Type",
    kind: "Kind",
    resource: "Resource",
    effect: "Effect",
    status: "Status",
    duration: "Duration",
    rule: "Rule",
    run: "Run",
};

const columnHelper = createColumnHelper<EventItem>();

function asEffect(value: string | null | undefined): EffectKind | null {
    if (!value || !EFFECTS.has(value as EffectKind)) {
        return null;
    }
    return value as EffectKind;
}

function asStatus(value: string | null | undefined): StatusKind | null {
    if (!value || !STATUSES.has(value as StatusKind)) {
        return null;
    }
    return value as StatusKind;
}

function agentLabel(item: EventItem): string {
    if (typeof item.agent_name === "string" && item.agent_name) {
        return item.agent_name;
    }
    if (typeof item.agent === "string" && item.agent) {
        return item.agent;
    }
    return "—";
}

function durationMs(item: EventItem): number | null {
    const value = item.duration_ms;
    if (typeof value === "number" && Number.isFinite(value)) {
        return value;
    }
    return null;
}

function toVisibilityState(visibility: ExplorerColumnVisibility): VisibilityState {
    const state: VisibilityState = {};
    for (const id of EXPLORER_COLUMN_IDS) {
        state[id] = visibility[id];
    }
    return state;
}

/**
 * Virtualized explorer results table with column visibility and export.
 */
export function EventTable({
    items,
    selectedId,
    loading = false,
    loadingMore = false,
    onSelect,
    onEndReached,
    onExport,
    highlightedIds,
    liveBufferedCount = 0,
    onLiveResume,
    onViewportScroll,
    scrollResetToken,
    className,
}: EventTableProps) {
    const [visibility, setVisibility] = useState<ExplorerColumnVisibility>(() =>
        readColumnVisibility(),
    );
    const [menuOpen, setMenuOpen] = useState(false);

    const columns = useMemo(
        () => [
            columnHelper.accessor((row) => row.ts, {
                id: "time",
                header: "Time",
                size: 120,
                cell: (info) => {
                    const value = info.getValue();
                    return value ? <RelativeTime value={String(value)} /> : "—";
                },
            }),
            columnHelper.accessor((row) => agentLabel(row), {
                id: "agent",
                header: "Agent",
                size: 140,
                cell: (info) => info.getValue(),
            }),
            columnHelper.accessor((row) => row.type, {
                id: "type",
                header: "Type",
                size: 100,
                cell: (info) => {
                    const value = info.getValue();
                    return typeof value === "string" ? value : "—";
                },
            }),
            columnHelper.accessor((row) => row.kind, {
                id: "kind",
                header: "Kind",
                size: 90,
                cell: (info) => {
                    const value = info.getValue();
                    return typeof value === "string" ? value : "—";
                },
            }),
            columnHelper.accessor((row) => row.resource, {
                id: "resource",
                header: "Resource",
                size: 160,
                cell: (info) => {
                    const value = info.getValue();
                    return typeof value === "string" ? <Mono>{value}</Mono> : "—";
                },
            }),
            columnHelper.accessor((row) => row.effect, {
                id: "effect",
                header: "Effect",
                size: 140,
                cell: (info) => {
                    const effect = asEffect(
                        typeof info.getValue() === "string" ? info.getValue() : null,
                    );
                    return effect ? <EffectBadge effect={effect} /> : "—";
                },
            }),
            columnHelper.accessor((row) => row.status, {
                id: "status",
                header: "Status",
                size: 110,
                cell: (info) => {
                    const status = asStatus(
                        typeof info.getValue() === "string" ? info.getValue() : null,
                    );
                    return status ? <StatusBadge status={status} /> : "—";
                },
            }),
            columnHelper.accessor((row) => durationMs(row), {
                id: "duration",
                header: "Duration",
                size: 90,
                cell: (info) => {
                    const value = info.getValue();
                    return value === null ? "—" : formatDuration(value);
                },
            }),
            columnHelper.accessor((row) => row.rule, {
                id: "rule",
                header: "Rule",
                size: 160,
                cell: (info) => {
                    const value = info.getValue();
                    return typeof value === "string" ? <Mono>{value}</Mono> : "—";
                },
            }),
            columnHelper.accessor((row) => row.run, {
                id: "run",
                header: "Run",
                size: 140,
                cell: (info) => {
                    const value = info.getValue();
                    if (typeof value !== "string" || !value) {
                        return "—";
                    }
                    return (
                        <Link
                            to="/traces/$runId"
                            params={{ runId: value }}
                            className="text-primary hover:underline"
                            onClick={(event) => event.stopPropagation()}
                        >
                            <Mono>{value}</Mono>
                        </Link>
                    );
                },
            }),
        ],
        [],
    );

    return (
        <div className={cn("space-y-2", className)} data-testid="explorer-results">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <p className="text-muted-foreground text-xs">
                        {items.length} loaded
                        {loadingMore ? " · loading more…" : ""}
                    </p>
                    {liveBufferedCount > 0 && onLiveResume ? (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="explorer-live-resume"
                            onClick={onLiveResume}
                        >
                            {newEventsLabel(liveBufferedCount)}
                        </Button>
                    ) : null}
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="explorer-columns"
                            aria-expanded={menuOpen}
                            onClick={() => setMenuOpen((open) => !open)}
                        >
                            Columns
                        </Button>
                        {menuOpen ? (
                            <div
                                className="border-border bg-panel absolute right-0 z-20 mt-1 w-44 rounded-md border p-2 shadow-sm"
                                data-testid="explorer-columns-menu"
                            >
                                {EXPLORER_COLUMN_IDS.map((id) => (
                                    <label
                                        key={id}
                                        className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm"
                                    >
                                        <input
                                            type="checkbox"
                                            className="accent-primary"
                                            checked={visibility[id]}
                                            data-testid={`explorer-column-${id}`}
                                            onChange={(event) => {
                                                const next = {
                                                    ...visibility,
                                                    [id]: event.target.checked,
                                                };
                                                if (
                                                    !EXPLORER_COLUMN_IDS.some(
                                                        (columnId) => next[columnId],
                                                    )
                                                ) {
                                                    return;
                                                }
                                                setVisibility(next);
                                                writeColumnVisibility(next);
                                            }}
                                        />
                                        {COLUMN_LABELS[id]}
                                    </label>
                                ))}
                            </div>
                        ) : null}
                    </div>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="explorer-export"
                        disabled={items.length === 0}
                        onClick={onExport}
                    >
                        Export NDJSON
                    </Button>
                </div>
            </div>

            <DataTable
                data={items}
                columns={columns}
                height={420}
                loading={loading && items.length === 0}
                emptyTitle="No events"
                emptyBody="Adjust filters or widen the time range."
                aria-label="Explorer events"
                getRowId={(row) => row.id}
                columnVisibility={toVisibilityState(visibility)}
                onColumnVisibilityChange={(updater) => {
                    setVisibility((current) => {
                        const state = toVisibilityState(current);
                        const nextState =
                            typeof updater === "function" ? updater(state) : updater;
                        const next = { ...current };
                        for (const id of EXPLORER_COLUMN_IDS) {
                            if (typeof nextState[id] === "boolean") {
                                next[id] = nextState[id] === true;
                            }
                        }
                        writeColumnVisibility(next);
                        return next;
                    });
                }}
                onRowActivate={(row) => onSelect(row.id)}
                rowClassName={(row) =>
                    cn(
                        selectedId === row.id ? "bg-muted" : undefined,
                        highlightedIds?.has(row.id) ? "explorer-row-live" : undefined,
                    )
                }
                getRowAttributes={(row) => ({
                    "data-testid": "explorer-row",
                    "data-event-id": row.id,
                    "data-agent": agentLabel(row),
                    "data-effect": typeof row.effect === "string" ? row.effect : "",
                    "data-resource":
                        typeof row.resource === "string" ? row.resource : "",
                    "data-live-highlight": highlightedIds?.has(row.id)
                        ? "1"
                        : undefined,
                })}
                onEndReached={onEndReached}
                onViewportScroll={onViewportScroll}
                scrollResetToken={scrollResetToken}
            />
        </div>
    );
}
