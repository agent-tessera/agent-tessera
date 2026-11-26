import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { meKeys, runsKeys } from "@/api/keys";
import { fetchMe } from "@/api/me";
import { fetchRunTrace, revokeRun } from "@/api/runs";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ErrorState";
import { Mono } from "@/components/Mono";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { EffectsTab } from "@/components/traces/EffectsTab";
import { EventsTab } from "@/components/traces/EventsTab";
import { SpanDrawer } from "@/components/traces/SpanDrawer";
import { TraceTree } from "@/components/traces/TraceTree";
import { TraceWaterfall } from "@/components/traces/TraceWaterfall";
import { UnparentedList } from "@/components/traces/UnparentedList";
import { formatDuration, runStatusKind } from "@/lib/overview-metrics";
import { setPageTitle } from "@/lib/page-title";
import { cn } from "@/lib/utils";

export type TraceDetailSearch = {
    span?: string;
    environment?: string;
    tab?: "waterfall" | "tree" | "effects" | "events";
};

function parseTraceSearch(search: Record<string, unknown>): TraceDetailSearch {
    const out: TraceDetailSearch = {};
    if (typeof search.span === "string" && search.span.trim()) {
        out.span = search.span.trim();
    } else if (typeof search.span === "number" && Number.isFinite(search.span)) {
        // Legacy JSON-search bookmarks may still encode all-digit spans as numbers.
        out.span = String(Math.trunc(search.span));
    }
    if (typeof search.environment === "string" && search.environment.trim()) {
        out.environment = search.environment.trim();
    }
    if (
        search.tab === "tree" ||
        search.tab === "waterfall" ||
        search.tab === "effects" ||
        search.tab === "events"
    ) {
        out.tab = search.tab;
    }
    return out;
}

export const Route = createFileRoute("/_console/traces/$runId")({
    validateSearch: parseTraceSearch,
    component: TraceDetailPage,
});

const LIVE_REFETCH_MS = 3_000;

type TraceTab = NonNullable<TraceDetailSearch["tab"]>;

/**
 * Governance trace detail: header, tabs, span drawer, revoke.
 */
function TraceDetailPage() {
    const { runId } = Route.useParams();
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const queryClient = useQueryClient();
    const tab: TraceTab =
        search.tab === "tree" || search.tab === "effects" || search.tab === "events"
            ? search.tab
            : "waterfall";
    const [localSelected, setLocalSelected] = useState<string | undefined>(search.span);
    const [drawerOpen, setDrawerOpen] = useState(Boolean(search.span));
    const [revokeOpen, setRevokeOpen] = useState(false);
    const [revokeError, setRevokeError] = useState<string | null>(null);

    useEffect(() => {
        setPageTitle(`Trace ${runId}`);
    }, [runId]);

    useEffect(() => {
        setLocalSelected(search.span);
        setDrawerOpen(Boolean(search.span));
    }, [search.span]);

    const meQuery = useQuery({
        queryKey: meKeys.all,
        queryFn: fetchMe,
        staleTime: 60_000,
    });

    const traceQuery = useQuery({
        queryKey: runsKeys.trace(runId),
        queryFn: () => fetchRunTrace(runId),
        refetchInterval: (query) =>
            query.state.data?.run.status === "running" ? LIVE_REFETCH_MS : false,
    });

    const revokeMutation = useMutation({
        mutationFn: (reason: string) => revokeRun(runId, { reason }),
        onSuccess: async () => {
            setRevokeOpen(false);
            setRevokeError(null);
            await queryClient.invalidateQueries({ queryKey: runsKeys.trace(runId) });
            await queryClient.invalidateQueries({ queryKey: runsKeys.all });
        },
        onError: (error) => {
            setRevokeError(error instanceof Error ? error.message : "Revoke failed");
        },
    });

    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of traceQuery.data?.agents ?? []) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [traceQuery.data?.agents]);

    const selectedSpan = useMemo(() => {
        if (!localSelected || !traceQuery.data) {
            return null;
        }
        return (
            traceQuery.data.spans.find((span) => span.span_id === localSelected) ?? null
        );
    }, [localSelected, traceQuery.data]);

    const selectSpan = (spanId: string) => {
        setLocalSelected(spanId);
        setDrawerOpen(true);
        void navigate({
            search: (prev) => ({ ...prev, span: spanId }),
            replace: true,
        });
    };

    const closeDrawer = () => {
        setDrawerOpen(false);
        void navigate({
            search: (prev) => {
                const out = { ...prev };
                delete out.span;
                return out;
            },
            replace: true,
        });
    };

    const setTab = (next: TraceTab) => {
        void navigate({
            search: (prev) => {
                const out = { ...prev };
                if (next === "waterfall") {
                    delete out.tab;
                } else {
                    out.tab = next;
                }
                return out;
            },
            replace: true,
        });
    };

    if (traceQuery.isError) {
        return (
            <div className="space-y-6" data-testid="trace-detail">
                <PageHeader
                    title="Trace"
                    breadcrumbs={[
                        { label: "Console", href: "/app/overview" },
                        { label: "Traces", href: "/app/traces" },
                        { label: runId },
                    ]}
                />
                <ErrorState
                    title="Could not load trace"
                    message={
                        traceQuery.error instanceof Error
                            ? traceQuery.error.message
                            : "Request failed"
                    }
                    onRetry={() => void traceQuery.refetch()}
                />
            </div>
        );
    }

    const trace = traceQuery.data;
    const run = trace?.run;
    const startMs = run ? Date.parse(run.first_seen_at) : Number.NaN;
    const endMs = run ? Date.parse(run.last_seen_at) : Number.NaN;
    const durationLabel = formatDuration(
        Number.isFinite(startMs) && Number.isFinite(endMs)
            ? endMs - startMs
            : Number.NaN,
    );
    const principal = run?.principal?.trim() ? run.principal : "—";
    const supervisor = run?.supervisor?.trim() ? run.supervisor : "—";
    const startedLabel =
        run && Number.isFinite(startMs)
            ? new Date(startMs).toISOString().replace(/\.\d{3}Z$/, "Z")
            : "—";
    const canRevoke = run?.status !== "revoked";

    return (
        <div className="space-y-4" data-testid="trace-detail">
            <PageHeader
                title="Trace"
                description={
                    trace?.truncated
                        ? "Span list truncated at 5,000 (API cap)."
                        : undefined
                }
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Traces", href: "/app/traces" },
                    { label: runId },
                ]}
                actions={
                    <Button
                        type="button"
                        variant="outline"
                        disabled={!canRevoke || revokeMutation.isPending}
                        data-testid="trace-revoke"
                        onClick={() => {
                            setRevokeError(null);
                            setRevokeOpen(true);
                        }}
                    >
                        Revoke run
                    </Button>
                }
            />

            <dl
                className="border-border bg-panel grid gap-3 rounded-md border p-3 text-sm sm:grid-cols-2 lg:grid-cols-3"
                data-testid="trace-header"
            >
                <HeaderItem label="Run">
                    <span data-testid="trace-run-id">
                        <Mono>{runId}</Mono>
                    </span>
                </HeaderItem>
                <HeaderItem label="Status">
                    {run ? (
                        <span data-testid="trace-status">
                            <StatusBadge status={runStatusKind(run.status)} />
                        </span>
                    ) : (
                        <span className="text-muted-foreground">Loading…</span>
                    )}
                </HeaderItem>
                <HeaderItem label="Principal">
                    <span data-testid="trace-principal">{principal}</span>
                </HeaderItem>
                <HeaderItem label="Supervisor">
                    <span data-testid="trace-supervisor">{supervisor}</span>
                </HeaderItem>
                <HeaderItem label="Started">
                    <time
                        dateTime={run?.first_seen_at}
                        className="tabular-nums"
                        data-testid="trace-started"
                    >
                        {startedLabel}
                    </time>
                </HeaderItem>
                <HeaderItem label="Duration">
                    <span className="tabular-nums" data-testid="trace-duration">
                        {durationLabel}
                    </span>
                </HeaderItem>
            </dl>

            {revokeError ? (
                <ErrorState
                    title="Revoke failed"
                    message={revokeError}
                    onRetry={() => setRevokeOpen(true)}
                    retryLabel="Try again"
                />
            ) : null}

            <div
                className="border-border flex flex-wrap gap-1 border-b"
                role="tablist"
                aria-label="Trace views"
            >
                <TabButton
                    active={tab === "waterfall"}
                    onClick={() => setTab("waterfall")}
                    testId="trace-tab-waterfall"
                >
                    Waterfall
                </TabButton>
                <TabButton
                    active={tab === "tree"}
                    onClick={() => setTab("tree")}
                    testId="trace-tab-tree"
                >
                    Tree
                </TabButton>
                <TabButton
                    active={tab === "effects"}
                    onClick={() => setTab("effects")}
                    testId="trace-tab-effects"
                >
                    Effects
                </TabButton>
                <TabButton
                    active={tab === "events"}
                    onClick={() => setTab("events")}
                    testId="trace-tab-events"
                >
                    Events
                </TabButton>
            </div>

            {traceQuery.isLoading || !trace ? (
                <div
                    className="bg-muted h-64 animate-pulse rounded-md"
                    aria-busy="true"
                    aria-label="Loading trace"
                />
            ) : tab === "tree" ? (
                <TraceTree
                    spans={trace.spans}
                    agents={trace.agents}
                    selectedSpanId={localSelected}
                    onSelectSpan={selectSpan}
                />
            ) : tab === "effects" ? (
                <EffectsTab runId={runId} />
            ) : tab === "events" ? (
                <EventsTab runId={runId} />
            ) : (
                <>
                    <TraceWaterfall
                        spans={trace.spans}
                        agentNames={agentNames}
                        selectedSpanId={localSelected}
                        onSelectSpan={selectSpan}
                    />
                    <UnparentedList events={trace.unparented} />
                </>
            )}

            <SpanDrawer
                span={selectedSpan}
                open={drawerOpen && selectedSpan != null}
                onClose={closeDrawer}
                traceUrlTemplate={meQuery.data?.trace_url}
                agentName={
                    selectedSpan?.agent_id
                        ? agentNames[selectedSpan.agent_id]
                        : undefined
                }
            />

            <ConfirmDialog
                open={revokeOpen}
                title="Revoke run"
                sentence={`Revoke run ${runId}? Agents in this run will be denied further actions.`}
                confirmLabel="Revoke"
                reasonRequired
                destructive
                onCancel={() => setRevokeOpen(false)}
                onConfirm={(reason) => {
                    setRevokeError(null);
                    revokeMutation.mutate(reason);
                }}
            />

            <p className="text-muted-foreground text-xs">
                <Link
                    to="/traces"
                    search={(prev) => prev}
                    className="underline-offset-2 hover:underline"
                    data-testid="trace-back-to-list"
                >
                    Back to traces
                </Link>
            </p>
        </div>
    );
}

function HeaderItem({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="min-w-0 space-y-1">
            <dt className="text-muted-foreground text-xs tracking-wide uppercase">
                {label}
            </dt>
            <dd className="min-w-0">{children}</dd>
        </div>
    );
}

function TabButton({
    active,
    onClick,
    children,
    testId,
}: {
    active: boolean;
    onClick: () => void;
    children: ReactNode;
    testId: string;
}) {
    return (
        <button
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={testId}
            className={cn(
                "px-3 py-2 text-sm font-medium",
                active
                    ? "border-primary text-foreground border-b-2"
                    : "text-muted-foreground hover:text-foreground",
            )}
            onClick={onClick}
        >
            {children}
        </button>
    );
}
