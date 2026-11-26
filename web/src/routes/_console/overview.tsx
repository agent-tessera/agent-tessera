import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, getRouteApi } from "@tanstack/react-router";
import { useEffect, useMemo, useRef } from "react";

import {
    agentsKeys,
    approvalsKeys,
    auditKeys,
    dashboardsKeys,
    eventsKeys,
    postureKeys,
    revocationsKeys,
    runsKeys,
} from "@/api/keys";
import {
    fetchAgents,
    fetchAuditVerify,
    fetchDashboardSummary,
    fetchDenialsDashboard,
    fetchEventHistogram,
    fetchPendingApprovalsList,
    fetchPostureBoard,
    fetchRevocations,
    fetchRuns,
    fetchSpendDashboard,
    type AgentListItem,
    type ApprovalListItem,
} from "@/api/overview";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { FleetActivity } from "@/components/overview/FleetActivity";
import { KpiCard } from "@/components/overview/KpiCard";
import { NeedsAttention } from "@/components/overview/NeedsAttention";
import { OnboardingCard } from "@/components/overview/OnboardingCard";
import { RecentRuns } from "@/components/overview/RecentRuns";
import {
    approvalsOldestFirst,
    errorRate,
    findDenialSpikes,
    formatDelta,
    formatRate,
    formatSpend,
    lowPostureAgents,
    relativeDelta,
    sparklineValues,
    sumSeries,
    sumSpend,
    type AttentionItem,
} from "@/lib/overview-metrics";
import { setPageTitle } from "@/lib/page-title";

const consoleRoute = getRouteApi("/_console");

export const Route = createFileRoute("/_console/overview")({
    component: OverviewPage,
});

const MS_HOUR = 3_600_000;
const MS_DAY = 24 * MS_HOUR;

const LIFECYCLE_KINDS = new Set([
    "agent_suspended",
    "agent_activated",
    "instance_revoked",
    "policy_updated",
]);

function OverviewPage() {
    const { environment } = consoleRoute.useSearch();
    const queryClient = useQueryClient();
    const seenLifecycleIds = useRef<Set<number>>(new Set());

    useEffect(() => {
        setPageTitle("Overview");
    }, []);

    const now = useMemo(() => new Date(), []);
    const dayAgo = useMemo(() => new Date(now.getTime() - MS_DAY), [now]);
    const twoDaysAgo = useMemo(() => new Date(now.getTime() - 2 * MS_DAY), [now]);
    const hourAgo = useMemo(() => new Date(now.getTime() - MS_HOUR), [now]);

    const summaryQuery = useQuery({
        queryKey: dashboardsKeys.summary({ environment }),
        queryFn: fetchDashboardSummary,
    });

    const histCurrentQuery = useQuery({
        queryKey: eventsKeys.histogram({
            window: "24h",
            buckets: 24,
            environment,
        }),
        queryFn: () => fetchEventHistogram({ since: dayAgo, until: now, buckets: 24 }),
    });

    const histPrevQuery = useQuery({
        queryKey: eventsKeys.histogram({
            window: "prev24h",
            buckets: 24,
            environment,
        }),
        queryFn: () =>
            fetchEventHistogram({
                since: twoDaysAgo,
                until: dayAgo,
                buckets: 24,
            }),
    });

    const denialsHourQuery = useQuery({
        queryKey: dashboardsKeys.denials({ window: "1h", environment }),
        queryFn: () => fetchDenialsDashboard(hourAgo, now),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const denialsDayQuery = useQuery({
        queryKey: dashboardsKeys.denials({ window: "24h", environment }),
        queryFn: () => fetchDenialsDashboard(dayAgo, now),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const spendPrevQuery = useQuery({
        queryKey: dashboardsKeys.spend({ window: "prev24h", environment }),
        queryFn: () => fetchSpendDashboard(twoDaysAgo, dayAgo),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const runsCurrentQuery = useQuery({
        queryKey: runsKeys.list({ since: "24h", limit: 500, environment }),
        queryFn: () => fetchRuns({ since: dayAgo, until: now, limit: 500 }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const runsPrevQuery = useQuery({
        queryKey: runsKeys.list({ since: "prev24h", limit: 500, environment }),
        queryFn: () => fetchRuns({ since: twoDaysAgo, until: dayAgo, limit: 500 }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const recentRunsQuery = useQuery({
        queryKey: runsKeys.list({ limit: 10, environment }),
        queryFn: () => fetchRuns({ limit: 10 }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const approvalsQuery = useQuery({
        queryKey: approvalsKeys.list({ status: "pending", environment }),
        queryFn: () => fetchPendingApprovalsList(50),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const orphanedQuery = useQuery({
        queryKey: agentsKeys.list({
            orphaned: true,
            environment,
            limit: 50,
        }),
        queryFn: () =>
            fetchAgents({
                orphaned: true,
                environment,
                limit: 50,
            }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const agentsQuery = useQuery({
        queryKey: agentsKeys.list({ environment, limit: 200 }),
        queryFn: () => fetchAgents({ environment, limit: 200 }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const postureQuery = useQuery({
        queryKey: postureKeys.summary({ environment }),
        queryFn: fetchPostureBoard,
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const auditQuery = useQuery({
        queryKey: auditKeys.verify(),
        queryFn: fetchAuditVerify,
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
    });

    const revocationsQuery = useQuery({
        queryKey: revocationsKeys.list({ limit: 15 }),
        queryFn: () => fetchRevocations({ limit: 15 }),
        enabled: (summaryQuery.data?.inventory.total ?? 0) > 0,
        refetchInterval: 12_000,
    });

    useEffect(() => {
        const items = revocationsQuery.data?.items ?? [];
        if (items.length === 0) {
            return;
        }
        const seen = seenLifecycleIds.current;
        let shouldInvalidate = false;
        for (const item of items) {
            if (seen.has(item.id)) {
                continue;
            }
            seen.add(item.id);
            if (LIFECYCLE_KINDS.has(item.kind)) {
                shouldInvalidate = true;
            }
        }
        if (shouldInvalidate) {
            void queryClient.invalidateQueries({ queryKey: agentsKeys.all });
            void queryClient.invalidateQueries({
                queryKey: dashboardsKeys.summary({ environment }),
            });
        }
    }, [revocationsQuery.data, queryClient, environment]);

    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of agentsQuery.data?.items ?? []) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [agentsQuery.data]);

    const attentionItems = useMemo(() => {
        return buildAttention({
            approvals: approvalsQuery.data?.items ?? [],
            denialsHour: denialsHourQuery.data,
            denialsDay: denialsDayQuery.data,
            orphaned: orphanedQuery.data?.items ?? [],
            dormantCount: summaryQuery.data?.dormant ?? 0,
            posture: postureQuery.data ?? [],
            auditOk: auditQuery.data?.ok,
            auditError: auditQuery.data?.error,
            agentNames,
        });
    }, [
        approvalsQuery.data,
        denialsHourQuery.data,
        denialsDayQuery.data,
        orphanedQuery.data,
        summaryQuery.data?.dormant,
        postureQuery.data,
        auditQuery.data,
        agentNames,
    ]);

    if (summaryQuery.isError) {
        return (
            <div className="space-y-6">
                <PageHeader title="Overview" />
                <ErrorState
                    message={
                        summaryQuery.error instanceof Error
                            ? summaryQuery.error.message
                            : "Failed to load overview"
                    }
                    onRetry={() => void summaryQuery.refetch()}
                />
            </div>
        );
    }

    if (summaryQuery.isPending || !summaryQuery.data) {
        return (
            <div className="space-y-6">
                <PageHeader title="Overview" />
                <div
                    className="bg-muted h-24 animate-pulse rounded-md"
                    aria-busy="true"
                    aria-label="Loading overview"
                />
            </div>
        );
    }

    const summary = summaryQuery.data;
    const isFirstRun = summary.inventory.total === 0;

    if (isFirstRun) {
        return (
            <div className="space-y-6" data-testid="overview-first-run">
                <PageHeader
                    title="Overview"
                    description="Get started with your first governed agent."
                />
                <OnboardingCard />
            </div>
        );
    }

    const series = histCurrentQuery.data?.series;
    const prevSeries = histPrevQuery.data?.series;
    const active = summary.inventory.active;
    const total = summary.inventory.total;
    const pending = summary.pending_approvals;
    const denials = summary.denials_24h;
    const spend = summary.spend_24h;
    const runs24h = runsCurrentQuery.data?.items.length ?? 0;
    const runsPrev = runsPrevQuery.data?.items.length ?? 0;
    const rate = errorRate(series);
    const prevRate = errorRate(prevSeries);
    const prevDenials = sumSeries(prevSeries, "deny");
    const prevSpend = sumSpend(spendPrevQuery.data?.items);
    const prevPending = sumSeries(prevSeries, "require_approval");

    const deltaAgents = relativeDelta(
        sumSeries(series, "total"),
        sumSeries(prevSeries, "total"),
    );
    const deltaPending = relativeDelta(pending, prevPending);
    const deltaRuns = relativeDelta(runs24h, runsPrev);
    const deltaDenials = relativeDelta(denials, prevDenials);
    const deltaSpend = relativeDelta(spend, prevSpend);
    const deltaRate = relativeDelta(rate, prevRate);

    return (
        <div className="space-y-8" data-testid="overview-populated">
            <PageHeader
                title="Overview"
                description="Inventory health, attention items, and recent runs."
            />

            <section
                data-testid="kpi-row"
                className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6"
                aria-label="Key metrics"
            >
                <KpiCard
                    label="Agents"
                    value={
                        <span>
                            {active}
                            <span className="text-muted-foreground text-base font-normal">
                                {" "}
                                / {total}
                            </span>
                        </span>
                    }
                    deltaLabel={formatDelta(deltaAgents)}
                    deltaPositive={deltaAgents === null ? null : deltaAgents >= 0}
                    sparkline={sparklineValues(series, "total")}
                    sparklineLabel="Agent activity 24h"
                    to="/agents"
                />
                <KpiCard
                    label="Pending approvals"
                    value={pending}
                    deltaLabel={formatDelta(deltaPending)}
                    deltaPositive={deltaPending === null ? null : deltaPending <= 0}
                    sparkline={sparklineValues(series, "require_approval")}
                    sparklineLabel="Approval decisions 24h"
                    to="/approvals"
                    search={{ status: "pending" }}
                />
                <KpiCard
                    label="Runs 24h"
                    value={runs24h}
                    deltaLabel={formatDelta(deltaRuns)}
                    deltaPositive={deltaRuns === null ? null : deltaRuns >= 0}
                    sparkline={sparklineValues(series, "total")}
                    sparklineLabel="Event volume 24h"
                    to="/traces"
                />
                <KpiCard
                    label="Denials 24h"
                    value={denials}
                    deltaLabel={formatDelta(deltaDenials)}
                    deltaPositive={deltaDenials === null ? null : deltaDenials <= 0}
                    sparkline={sparklineValues(series, "deny")}
                    sparklineLabel="Denials 24h"
                    to="/explorer"
                    search={{ effect: "deny" }}
                />
                <KpiCard
                    label="Spend 24h"
                    value={formatSpend(spend)}
                    deltaLabel={formatDelta(deltaSpend)}
                    deltaPositive={deltaSpend === null ? null : deltaSpend <= 0}
                    sparkline={sparklineValues(series, "total")}
                    sparklineLabel="Activity proxy for spend"
                    to="/insights"
                    search={{ view: "spend" }}
                />
                <KpiCard
                    label="Error rate 24h"
                    value={formatRate(rate)}
                    deltaLabel={formatDelta(deltaRate)}
                    deltaPositive={deltaRate === null ? null : deltaRate <= 0}
                    sparkline={sparklineValues(series, "error")}
                    sparklineLabel="Errors 24h"
                    to="/explorer"
                    search={{ status: "error" }}
                />
            </section>

            <div className="grid gap-8 xl:grid-cols-3">
                <NeedsAttention items={attentionItems} />
                <RecentRuns
                    runs={recentRunsQuery.data?.items ?? []}
                    agentNames={agentNames}
                    loading={recentRunsQuery.isPending}
                />
                <FleetActivity
                    items={revocationsQuery.data?.items ?? []}
                    agentNames={agentNames}
                    loading={revocationsQuery.isPending}
                />
            </div>
        </div>
    );
}

function buildAttention(input: {
    approvals: ApprovalListItem[];
    denialsHour: Awaited<ReturnType<typeof fetchDenialsDashboard>> | undefined;
    denialsDay: Awaited<ReturnType<typeof fetchDenialsDashboard>> | undefined;
    orphaned: AgentListItem[];
    dormantCount: number;
    posture: Awaited<ReturnType<typeof fetchPostureBoard>>;
    auditOk: boolean | undefined;
    auditError: string | undefined;
    agentNames: Record<string, string>;
}): AttentionItem[] {
    const items: AttentionItem[] = [];

    for (const approval of approvalsOldestFirst(input.approvals)) {
        const action =
            approval.action?.name ??
            approval.action?.resource ??
            approval.action?.type ??
            "action";
        const agentLabel = input.agentNames[approval.agent_id] ?? approval.agent_id;
        items.push({
            id: `approval:${approval.id}`,
            kind: "approval",
            title: `Pending approval · ${agentLabel}`,
            detail: `${action} · waiting since ${approval.created_at}`,
            to: "/approvals",
            search: { status: "pending", id: approval.id },
        });
    }

    if (input.denialsHour && input.denialsDay) {
        for (const spike of findDenialSpikes(input.denialsHour, input.denialsDay)) {
            items.push({
                id: `spike:${spike.agent_id}`,
                kind: "denial_spike",
                title: `Denial spike · ${spike.name}`,
                detail: `${spike.denials_1h} in the last hour (24h hourly mean ${spike.hourly_mean.toFixed(2)})`,
                to: "/agents/$agentId",
                params: { agentId: spike.agent_id },
            });
        }
    }

    for (const agent of input.orphaned) {
        items.push({
            id: `orphaned:${agent.id}`,
            kind: "orphaned",
            title: `Orphaned agent · ${agent.name}`,
            detail: "Owner is missing or inactive",
            to: "/agents/$agentId",
            params: { agentId: agent.id },
        });
    }

    if (input.dormantCount > 0) {
        items.push({
            id: "dormant:summary",
            kind: "dormant",
            title:
                input.dormantCount === 1
                    ? "1 dormant agent"
                    : `${input.dormantCount} dormant agents`,
            detail: "No events in the last 7 days",
            to: "/agents",
            search: { dormant: 1 },
        });
    }

    for (const row of lowPostureAgents(input.posture)) {
        items.push({
            id: `posture:${row.id}`,
            kind: "posture",
            title: `Low posture · ${row.name}`,
            detail: `Score ${row.score} (below 60)`,
            to: "/agents/$agentId",
            params: { agentId: row.id },
        });
    }

    if (input.auditOk === false) {
        items.push({
            id: "audit:verify",
            kind: "audit",
            title: "Audit verification failed",
            detail: input.auditError ?? "Hash chain integrity check failed",
            to: "/settings/audit",
        });
    }

    return items;
}
