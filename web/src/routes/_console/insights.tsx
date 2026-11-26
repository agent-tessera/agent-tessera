import { useQueries, useQuery } from "@tanstack/react-query";
import {
    Link,
    createFileRoute,
    getRouteApi,
    useNavigate,
} from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { fetchAgents } from "@/api/agents";
import { fetchEvents } from "@/api/events";
import {
    fetchInsightsDenials,
    fetchInsightsFootprint,
    fetchInsightsPosture,
    fetchInsightsQuotas,
    fetchSpendDashboard,
} from "@/api/insights";
import {
    agentsKeys,
    dashboardsKeys,
    eventsKeys,
    postureKeys,
    quotasKeys,
} from "@/api/keys";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { BreakdownBars } from "@/components/insights/BreakdownBars";
import { InsightsToolbar } from "@/components/insights/InsightsToolbar";
import { StackedSpendChart } from "@/components/insights/StackedSpendChart";
import { insightsWindow, spendTimeBuckets } from "@/lib/insights-range";
import {
    denialAgentRows,
    denialRuleRows,
    filterPostureRows,
    formatCo2eG,
    formatEnergyJ,
    spendRows,
    stackSpendBuckets,
    sumSpend,
    weakestComponentLabel,
} from "@/lib/insights-metrics";
import {
    parseInsightsSearch,
    resolveDenialBy,
    resolveInsightsRange,
    resolveInsightsView,
    resolveSpendGroupBy,
    type InsightsSearch,
} from "@/lib/insights-search";
import { formatMoney } from "@/lib/money";
import { setPageTitle } from "@/lib/page-title";
import { cn } from "@/lib/utils";

const consoleRoute = getRouteApi("/_console");

export type { InsightsSearch };

export const Route = createFileRoute("/_console/insights")({
    validateSearch: parseInsightsSearch,
    component: InsightsPage,
});

/**
 * Insights: spend, denials, footprint, posture, and quotas with shared env.
 */
function InsightsPage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const { environment: shellEnvironment } = consoleRoute.useSearch();
    const environment = search.environment ?? shellEnvironment;

    const view = resolveInsightsView(search.view);
    const range = resolveInsightsRange(search.range);
    const groupBy = resolveSpendGroupBy(search.group_by);
    const denialBy = resolveDenialBy(search.denial_by);

    useEffect(() => {
        setPageTitle("Insights");
    }, []);

    const patchSearch = (patch: Partial<InsightsSearch>) => {
        void navigate({
            search: (prev) => {
                const next: InsightsSearch = { ...prev, ...patch };
                for (const key of Object.keys(patch) as Array<keyof InsightsSearch>) {
                    if (patch[key] === undefined || patch[key] === "") {
                        delete next[key];
                    }
                }
                if (environment && !next.environment) {
                    next.environment = environment;
                }
                return next;
            },
            replace: true,
        });
    };

    const now = useMemo(() => new Date(), []);
    const timeWindow = useMemo(() => insightsWindow(range, now), [range, now]);
    const buckets = useMemo(() => spendTimeBuckets(timeWindow), [timeWindow]);

    const agentsQuery = useQuery({
        queryKey: agentsKeys.list({ environment, limit: 500 }),
        queryFn: () => fetchAgents({ environment, limit: 500 }),
    });

    const allowedAgentIds = useMemo(() => {
        if (!environment) {
            return null;
        }
        return new Set((agentsQuery.data?.items ?? []).map((agent) => agent.id));
    }, [environment, agentsQuery.data]);

    const spendQuery = useQuery({
        queryKey: dashboardsKeys.spend({
            range,
            group_by: groupBy,
            environment,
            from: timeWindow.from.toISOString(),
            to: timeWindow.to.toISOString(),
        }),
        queryFn: () => fetchSpendDashboard(timeWindow.from, timeWindow.to, groupBy),
        enabled: view === "spend",
    });

    const spendBucketQueries = useQueries({
        queries: buckets.map((bucket, index) => ({
            queryKey: dashboardsKeys.spend({
                range,
                group_by: groupBy,
                environment,
                bucket: index,
                from: bucket.from.toISOString(),
                to: bucket.to.toISOString(),
            }),
            queryFn: () => fetchSpendDashboard(bucket.from, bucket.to, groupBy),
            enabled: view === "spend",
        })),
    });

    const denialsQuery = useQuery({
        queryKey: dashboardsKeys.denials({
            range,
            environment,
            from: timeWindow.from.toISOString(),
            to: timeWindow.to.toISOString(),
        }),
        queryFn: () => fetchInsightsDenials(timeWindow.from, timeWindow.to),
        enabled: view === "denials" && denialBy === "agent",
    });

    const denialEventsQuery = useQuery({
        queryKey: eventsKeys.list({
            effect: ["deny"],
            since: timeWindow.from.toISOString(),
            until: timeWindow.to.toISOString(),
            limit: 500,
            environment,
        }),
        queryFn: () =>
            fetchEvents({
                effect: ["deny"],
                since: timeWindow.from.toISOString(),
                until: timeWindow.to.toISOString(),
                limit: 500,
            }),
        enabled: view === "denials" && denialBy === "rule",
    });

    const footprintQuery = useQuery({
        queryKey: [
            "footprint",
            "insights",
            range,
            timeWindow.from.toISOString(),
            timeWindow.to.toISOString(),
            environment,
        ],
        queryFn: () => fetchInsightsFootprint(timeWindow.from, timeWindow.to),
        enabled: view === "footprint",
    });

    const postureQuery = useQuery({
        queryKey: postureKeys.summary({ environment }),
        queryFn: fetchInsightsPosture,
        enabled: view === "posture",
    });

    const quotasQuery = useQuery({
        queryKey: quotasKeys.list({ environment }),
        queryFn: () => fetchInsightsQuotas({ environment }),
        enabled: view === "quotas",
    });

    const spendItems = spendRows(
        spendQuery.data,
        groupBy === "agent" ? allowedAgentIds : null,
    );
    const spendTotal = sumSpend(spendItems.map((row) => ({ cost_usd: row.cost_usd })));
    const stackedPoints = stackSpendBuckets(
        buckets,
        spendBucketQueries.map((query) => query.data),
        groupBy,
        groupBy === "agent" ? allowedAgentIds : null,
    );
    const bucketsLoading = spendBucketQueries.some((query) => query.isPending);

    const denialRows =
        denialBy === "rule"
            ? denialRuleRows(denialEventsQuery.data?.items ?? [], allowedAgentIds)
            : denialAgentRows(denialsQuery.data, allowedAgentIds);
    const denialTotal = denialRows.reduce((acc, row) => acc + row.denials, 0);

    const postureRows = filterPostureRows(postureQuery.data ?? [], allowedAgentIds);

    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of agentsQuery.data?.items ?? []) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [agentsQuery.data]);

    const quotaRows = useMemo(() => {
        const items = quotasQuery.data?.items ?? [];
        if (!allowedAgentIds) {
            return items;
        }
        return items.filter((row) => allowedAgentIds.has(row.agent_id));
    }, [quotasQuery.data, allowedAgentIds]);

    return (
        <div className="space-y-6" data-testid="insights-page">
            <PageHeader
                title="Insights"
                description="Spend, denials, footprint, posture, and quotas for the selected window."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Insights" },
                ]}
            />

            <InsightsToolbar
                view={view}
                range={range}
                onViewChange={(next) =>
                    patchSearch({ view: next === "spend" ? undefined : next })
                }
                onRangeChange={(next) =>
                    patchSearch({ range: next === "24h" ? undefined : next })
                }
            />

            {view === "spend" ? (
                <section className="space-y-4" data-testid="insights-spend">
                    <div className="flex flex-wrap items-end justify-between gap-3">
                        <div>
                            <p className="text-muted-foreground text-xs tracking-wide uppercase">
                                Total spend
                            </p>
                            <p
                                className="text-foreground font-mono text-2xl font-semibold"
                                data-testid="insights-spend-total"
                                data-spend-total={spendTotal}
                            >
                                {spendQuery.isPending ? "…" : formatMoney(spendTotal)}
                            </p>
                        </div>
                        <div
                            className="border-border flex gap-1 rounded-md border p-1"
                            role="group"
                            aria-label="Spend group by"
                        >
                            {(["agent", "model"] as const).map((option) => (
                                <button
                                    key={option}
                                    type="button"
                                    className={cn(
                                        "rounded px-2.5 py-1 text-xs font-medium",
                                        groupBy === option
                                            ? "bg-accent-soft text-foreground"
                                            : "text-muted-foreground hover:text-foreground",
                                    )}
                                    data-testid={`insights-spend-group-${option}`}
                                    onClick={() =>
                                        patchSearch({
                                            group_by:
                                                option === "agent" ? undefined : option,
                                        })
                                    }
                                >
                                    By {option}
                                </button>
                            ))}
                        </div>
                    </div>

                    {spendQuery.isError ? (
                        <ErrorState
                            message={
                                spendQuery.error instanceof Error
                                    ? spendQuery.error.message
                                    : "Failed to load spend"
                            }
                            onRetry={() => void spendQuery.refetch()}
                        />
                    ) : (
                        <>
                            <StackedSpendChart
                                points={stackedPoints}
                                loading={bucketsLoading}
                            />
                            <BreakdownBars
                                testId="insights-spend-bars"
                                rows={spendItems.map((row) => ({
                                    key: row.key,
                                    label: row.label,
                                    value: row.cost_usd,
                                    display: formatMoney(row.cost_usd),
                                }))}
                                emptyTitle="No spend in this window"
                                emptyBody="Usage cost appears after agents upload usage events."
                            />
                        </>
                    )}
                </section>
            ) : null}

            {view === "denials" ? (
                <section className="space-y-4" data-testid="insights-denials">
                    <div className="flex flex-wrap items-end justify-between gap-3">
                        <div>
                            <p className="text-muted-foreground text-xs tracking-wide uppercase">
                                Total denials
                            </p>
                            <p
                                className="text-foreground font-mono text-2xl font-semibold"
                                data-testid="insights-denials-total"
                            >
                                {denialBy === "agent"
                                    ? denialsQuery.isPending
                                        ? "…"
                                        : String(denialTotal)
                                    : denialEventsQuery.isPending
                                      ? "…"
                                      : String(denialTotal)}
                            </p>
                        </div>
                        <div
                            className="border-border flex gap-1 rounded-md border p-1"
                            role="group"
                            aria-label="Denials group by"
                        >
                            {(["agent", "rule"] as const).map((option) => (
                                <button
                                    key={option}
                                    type="button"
                                    className={cn(
                                        "rounded px-2.5 py-1 text-xs font-medium",
                                        denialBy === option
                                            ? "bg-accent-soft text-foreground"
                                            : "text-muted-foreground hover:text-foreground",
                                    )}
                                    data-testid={`insights-denials-group-${option}`}
                                    onClick={() =>
                                        patchSearch({
                                            denial_by:
                                                option === "agent" ? undefined : option,
                                        })
                                    }
                                >
                                    By {option}
                                </button>
                            ))}
                        </div>
                    </div>

                    {(denialBy === "agent" && denialsQuery.isError) ||
                    (denialBy === "rule" && denialEventsQuery.isError) ? (
                        <ErrorState
                            message="Failed to load denials"
                            onRetry={() => {
                                if (denialBy === "agent") {
                                    void denialsQuery.refetch();
                                } else {
                                    void denialEventsQuery.refetch();
                                }
                            }}
                        />
                    ) : (
                        <BreakdownBars
                            testId="insights-denials-bars"
                            rows={denialRows.map((row) => ({
                                key: row.key,
                                label: row.label,
                                value: row.denials,
                                display: String(row.denials),
                            }))}
                            emptyTitle="No denials in this window"
                            emptyBody="Denied decisions appear after agents hit a deny rule."
                        />
                    )}
                </section>
            ) : null}

            {view === "footprint" ? (
                <section className="space-y-4" data-testid="insights-footprint">
                    {footprintQuery.isError ? (
                        <ErrorState
                            message={
                                footprintQuery.error instanceof Error
                                    ? footprintQuery.error.message
                                    : "Failed to load footprint"
                            }
                            onRetry={() => void footprintQuery.refetch()}
                        />
                    ) : footprintQuery.isPending ? (
                        <div
                            className="border-border bg-panel h-40 animate-pulse rounded-md border"
                            aria-busy="true"
                        />
                    ) : (
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            <FootprintTile
                                label="Tokens in"
                                value={String(footprintQuery.data?.tokens_in ?? 0)}
                                testId="insights-footprint-tokens-in"
                            />
                            <FootprintTile
                                label="Tokens out"
                                value={String(footprintQuery.data?.tokens_out ?? 0)}
                                testId="insights-footprint-tokens-out"
                            />
                            <FootprintTile
                                label="Cost"
                                value={formatMoney(footprintQuery.data?.cost_usd ?? 0)}
                                testId="insights-footprint-cost"
                            />
                            <FootprintTile
                                label="Model calls"
                                value={String(footprintQuery.data?.model_calls ?? 0)}
                                testId="insights-footprint-model-calls"
                            />
                            <FootprintTile
                                label="Tool calls"
                                value={String(footprintQuery.data?.tool_calls ?? 0)}
                                testId="insights-footprint-tool-calls"
                            />
                            <FootprintTile
                                label="Denials"
                                value={String(footprintQuery.data?.denials ?? 0)}
                                testId="insights-footprint-denials"
                            />
                            <FootprintTile
                                label="Energy"
                                value={formatEnergyJ(footprintQuery.data?.energy_j)}
                                hint="estimate"
                                testId="insights-footprint-energy"
                            />
                            <FootprintTile
                                label="CO₂e"
                                value={formatCo2eG(footprintQuery.data?.co2e_g)}
                                hint="estimate"
                                testId="insights-footprint-co2e"
                            />
                        </div>
                    )}
                </section>
            ) : null}

            {view === "posture" ? (
                <section className="space-y-4" data-testid="insights-posture">
                    {postureQuery.isError ? (
                        <ErrorState
                            message={
                                postureQuery.error instanceof Error
                                    ? postureQuery.error.message
                                    : "Failed to load posture"
                            }
                            onRetry={() => void postureQuery.refetch()}
                        />
                    ) : postureQuery.isPending ? (
                        <div
                            className="border-border bg-panel h-40 animate-pulse rounded-md border"
                            aria-busy="true"
                        />
                    ) : postureRows.length === 0 ? (
                        <div
                            className="border-border bg-panel rounded-md border px-6 py-10 text-center"
                            data-testid="insights-posture-empty"
                        >
                            <h2 className="text-foreground text-sm font-semibold">
                                No posture yet
                            </h2>
                            <p className="text-muted-foreground mt-1 text-sm">
                                Scores appear after agents have a policy and report
                                events.
                            </p>
                        </div>
                    ) : (
                        <div className="border-border overflow-x-auto rounded-md border">
                            <table
                                className="w-full min-w-[32rem] text-left text-sm"
                                data-testid="insights-posture-table"
                            >
                                <thead className="bg-muted/40 text-muted-foreground text-xs uppercase">
                                    <tr>
                                        <th className="px-3 py-2 font-medium">Agent</th>
                                        <th className="px-3 py-2 font-medium">Score</th>
                                        <th className="px-3 py-2 font-medium">
                                            Coverage
                                        </th>
                                        <th className="px-3 py-2 font-medium">
                                            Weakest
                                        </th>
                                        <th className="px-3 py-2 font-medium">
                                            Permissive
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {postureRows.map((row) => (
                                        <tr
                                            key={row.id}
                                            className="border-border border-t"
                                            data-agent-id={row.id}
                                        >
                                            <td className="text-foreground px-3 py-2">
                                                <a
                                                    className="hover:underline"
                                                    href={`/app/agents/${encodeURIComponent(row.id)}`}
                                                >
                                                    {row.name}
                                                </a>
                                            </td>
                                            <td className="px-3 py-2 font-mono">
                                                {Math.round(Number(row.score) || 0)}
                                            </td>
                                            <td className="px-3 py-2 font-mono">
                                                {formatCoverage(row.coverage)}
                                            </td>
                                            <td className="px-3 py-2">
                                                {weakestComponentLabel(row)}
                                            </td>
                                            <td className="px-3 py-2">
                                                {row.permissive ? (
                                                    <span
                                                        className="bg-effect-deny-bg text-effect-deny-fg inline-flex rounded-md px-1.5 py-0.5 text-xs font-medium"
                                                        data-testid="insights-posture-permissive"
                                                    >
                                                        permissive
                                                    </span>
                                                ) : (
                                                    "—"
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </section>
            ) : null}

            {view === "quotas" ? (
                <section className="space-y-4" data-testid="insights-quotas">
                    {quotasQuery.isError ? (
                        <ErrorState
                            message={
                                quotasQuery.error instanceof Error
                                    ? quotasQuery.error.message
                                    : "Failed to load quotas"
                            }
                            onRetry={() => void quotasQuery.refetch()}
                        />
                    ) : quotasQuery.isPending ? (
                        <p className="text-muted-foreground text-sm">Loading…</p>
                    ) : quotaRows.length === 0 ? (
                        <EmptyState
                            title="No quotas"
                            body="Quotas appear when agents have tokens_per_day or named limits in their effective policy."
                            className="border-border bg-panel rounded-md border py-8"
                        />
                    ) : (
                        <ul className="border-border bg-panel divide-border divide-y rounded-md border">
                            {quotaRows.map((row) => {
                                const committed = row.used + row.outstanding;
                                const pct =
                                    row.max > 0
                                        ? Math.min(100, (committed / row.max) * 100)
                                        : 0;
                                const tone =
                                    pct >= 90
                                        ? "bg-effect-deny"
                                        : pct >= 70
                                          ? "bg-status-running"
                                          : "bg-status-ok";
                                return (
                                    <li
                                        key={`${row.agent_id}:${row.name}:${row.scope_key}:${row.window_start}`}
                                        className="space-y-2 px-3 py-3"
                                        data-testid={`quota-row-${row.agent_id}-${row.name}`}
                                    >
                                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                                            <div className="min-w-0 space-y-0.5">
                                                <p className="text-foreground text-sm font-medium">
                                                    <Link
                                                        to="/agents/$agentId"
                                                        params={{
                                                            agentId: row.agent_id,
                                                        }}
                                                        className="underline-offset-2 hover:underline"
                                                    >
                                                        {agentNames[row.agent_id] ??
                                                            row.agent_id}
                                                    </Link>
                                                    <span className="text-muted-foreground font-normal">
                                                        {" "}
                                                        ·{" "}
                                                    </span>
                                                    <span className="font-mono text-xs">
                                                        {row.name}
                                                    </span>
                                                </p>
                                                <p className="text-muted-foreground text-xs">
                                                    {row.metric} · {row.window} ·{" "}
                                                    {row.scope}
                                                </p>
                                            </div>
                                            <p className="font-mono text-xs tabular-nums">
                                                <span data-testid="quota-used">
                                                    {row.used}
                                                </span>
                                                {row.outstanding > 0 ? (
                                                    <span className="text-muted-foreground">
                                                        {" "}
                                                        +{row.outstanding}
                                                    </span>
                                                ) : null}
                                                <span className="text-muted-foreground">
                                                    {" "}
                                                    / {row.max}
                                                </span>
                                            </p>
                                        </div>
                                        <div
                                            className="bg-muted h-1.5 overflow-hidden rounded-full"
                                            role="progressbar"
                                            aria-valuenow={Math.round(pct)}
                                            aria-valuemin={0}
                                            aria-valuemax={100}
                                            aria-label={`${row.name} usage`}
                                        >
                                            <div
                                                className={cn(
                                                    "h-full rounded-full",
                                                    tone,
                                                )}
                                                style={{ width: `${pct}%` }}
                                            />
                                        </div>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </section>
            ) : null}
        </div>
    );
}

function FootprintTile({
    label,
    value,
    hint,
    testId,
}: {
    label: string;
    value: string;
    hint?: string;
    testId: string;
}) {
    return (
        <div
            className="border-border bg-panel rounded-md border p-4"
            data-testid={testId}
        >
            <div className="flex items-baseline justify-between gap-2">
                <p className="text-muted-foreground text-xs tracking-wide uppercase">
                    {label}
                </p>
                {hint ? (
                    <span
                        className="text-muted-foreground text-[10px] tracking-wide uppercase"
                        data-testid={`${testId}-estimate`}
                    >
                        {hint}
                    </span>
                ) : null}
            </div>
            <p className="text-foreground mt-1 font-mono text-xl font-semibold">
                {value}
            </p>
        </div>
    );
}

function formatCoverage(value: number | undefined): string {
    if (value === undefined || !Number.isFinite(value)) {
        return "—";
    }
    return `${Math.round(value * 100)}%`;
}
