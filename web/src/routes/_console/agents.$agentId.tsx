import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import {
    activateAgent,
    fetchAgent,
    fetchAgentPendingApprovalsCount,
    fetchAgentPolicyYaml,
    fetchAgentPosture,
    fetchFootprint,
    fetchOwners,
    retireAgent,
    suspendAgent,
} from "@/api/agents";
import { agentsKeys, ownersKeys, runsKeys } from "@/api/keys";
import { fetchRuns } from "@/api/runs";
import { AgentAccess } from "@/components/agents/AgentAccess";
import { AgentActivity } from "@/components/agents/AgentActivity";
import { AgentConnect } from "@/components/agents/AgentConnect";
import { AgentInstances } from "@/components/agents/AgentInstances";
import { AgentOverview } from "@/components/agents/AgentOverview";
import { AgentTraces } from "@/components/agents/AgentTraces";
import { LifecycleBadge } from "@/components/agents/LifecycleBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { RelativeTime } from "@/components/RelativeTime";
import { Button } from "@/components/ui/button";
import {
    activeAgentTab,
    AGENT_DETAIL_TABS,
    parseAgentDetailSearch,
    type AgentDetailSearch,
    type AgentDetailTab,
} from "@/lib/agent-detail-search";
import {
    lifecycleActionsForStatus,
    type LifecycleAction,
} from "@/lib/lifecycle-actions";
import { setPageTitle } from "@/lib/page-title";
import { permissiveKinds } from "@/lib/permissive-policy";
import { cn } from "@/lib/utils";

export type { AgentDetailSearch };

export const Route = createFileRoute("/_console/agents/$agentId")({
    validateSearch: parseAgentDetailSearch,
    component: AgentDetailPage,
});

const RECENT_RUNS = 10;

/** CodeMirror policy editor — code-split for the M6 initial-JS budget. */
const AgentPolicy = lazy(() =>
    import("@/components/agents/AgentPolicy").then((mod) => ({
        default: mod.AgentPolicy,
    })),
);

/**
 * Agent detail with Overview / Activity / Traces / Policy / Access /
 * Instances / Connect tabs.
 */
function AgentDetailPage() {
    const { agentId } = Route.useParams();
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const queryClient = useQueryClient();
    const tab = activeAgentTab(search);

    const [pendingAction, setPendingAction] = useState<LifecycleAction | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    const agentQuery = useQuery({
        queryKey: agentsKeys.detail(agentId),
        queryFn: () => fetchAgent(agentId),
    });

    const ownersQuery = useQuery({
        queryKey: ownersKeys.list({ limit: 200 }),
        queryFn: () => fetchOwners({ limit: 200 }),
    });

    const since24h = useMemo(() => {
        const date = new Date(Date.now() - 24 * 60 * 60 * 1000);
        return date.toISOString().replace(/\.\d{3}Z$/, "Z");
    }, []);

    const footprintQuery = useQuery({
        queryKey: ["footprint", agentId, since24h],
        queryFn: () => fetchFootprint({ agent_id: agentId, from: since24h }),
        enabled: Boolean(agentQuery.data),
    });

    const pendingQuery = useQuery({
        queryKey: ["approvals", "pending-count", agentId],
        queryFn: () => fetchAgentPendingApprovalsCount(agentId),
        enabled: Boolean(agentQuery.data),
    });

    const postureQuery = useQuery({
        queryKey: ["posture", "agent", agentId],
        queryFn: () => fetchAgentPosture(agentId),
        enabled: Boolean(agentQuery.data),
        retry: false,
    });

    const policyQuery = useQuery({
        queryKey: ["agents", "policy-yaml", agentId],
        queryFn: () => fetchAgentPolicyYaml(agentId),
        enabled: Boolean(agentQuery.data),
        retry: false,
    });

    const agentName = agentQuery.data?.name;
    const runsQuery = useQuery({
        queryKey: runsKeys.list({ agent: agentName, limit: RECENT_RUNS }),
        queryFn: () => fetchRuns({ agent: agentName, limit: RECENT_RUNS }),
        enabled: Boolean(agentName),
    });

    useEffect(() => {
        setPageTitle(agentQuery.data?.name ?? "Agent");
    }, [agentQuery.data?.name]);

    const setTab = (next: AgentDetailTab) => {
        void navigate({
            search: (prev) => {
                const out: AgentDetailSearch = { ...prev };
                if (next === "overview") {
                    delete out.section;
                } else {
                    out.section = next;
                }
                // Drop explorer-only params when leaving activity.
                if (next !== "activity") {
                    delete out.type;
                    delete out.effect;
                    delete out.status;
                    delete out.kind;
                    delete out.rule;
                    delete out.run;
                    delete out.span;
                    delete out.event;
                    delete out.since;
                    delete out.until;
                    delete out.live;
                    delete out.resource;
                    delete out.agent;
                }
                return out;
            },
            replace: true,
        });
    };

    const lifecycleMutation = useMutation({
        mutationFn: async (input: {
            action: LifecycleAction["name"];
            reason: string;
        }) => {
            if (input.action === "activate") {
                return activateAgent(agentId, { reason: input.reason });
            }
            if (input.action === "suspend") {
                return suspendAgent(agentId, { reason: input.reason });
            }
            return retireAgent(agentId, { reason: input.reason });
        },
        onSuccess: async () => {
            setPendingAction(null);
            setActionError(null);
            await queryClient.invalidateQueries({
                queryKey: agentsKeys.detail(agentId),
            });
            await queryClient.invalidateQueries({ queryKey: agentsKeys.all });
        },
        onError: (error) => {
            setActionError(
                error instanceof Error ? error.message : "Lifecycle action failed",
            );
        },
    });

    const ownerName = useMemo(() => {
        const ownerId = agentQuery.data?.owner_id;
        if (!ownerId) {
            return null;
        }
        const owner = ownersQuery.data?.items.find((item) => item.id === ownerId);
        return owner?.display_name ?? ownerId;
    }, [agentQuery.data?.owner_id, ownersQuery.data]);

    const actions = useMemo(() => {
        if (!agentQuery.data) {
            return [];
        }
        return lifecycleActionsForStatus(agentQuery.data.status, agentQuery.data.name);
    }, [agentQuery.data]);

    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        if (agentQuery.data) {
            map[agentQuery.data.id] = agentQuery.data.name;
        }
        return map;
    }, [agentQuery.data]);

    const permissive = useMemo(
        () => permissiveKinds(policyQuery.data ?? ""),
        [policyQuery.data],
    );

    if (agentQuery.isError) {
        return (
            <div className="space-y-6" data-testid="agent-detail">
                <PageHeader
                    title="Agent"
                    breadcrumbs={[
                        { label: "Console", href: "/app/overview" },
                        { label: "Agents", href: "/app/agents" },
                        { label: agentId },
                    ]}
                />
                <ErrorState
                    title="Could not load agent"
                    message={
                        agentQuery.error instanceof Error
                            ? agentQuery.error.message
                            : "Request failed"
                    }
                    onRetry={() => void agentQuery.refetch()}
                />
            </div>
        );
    }

    if (agentQuery.isPending || !agentQuery.data) {
        return (
            <div className="space-y-6" data-testid="agent-detail">
                <PageHeader
                    title="Agent"
                    breadcrumbs={[
                        { label: "Console", href: "/app/overview" },
                        { label: "Agents", href: "/app/agents" },
                        { label: agentId },
                    ]}
                />
                <p className="text-muted-foreground text-sm">Loading…</p>
            </div>
        );
    }

    const agent = agentQuery.data;

    return (
        <div className="space-y-6" data-testid="agent-detail">
            <PageHeader
                title={agent.name}
                description={agent.description || undefined}
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Agents", href: "/app/agents" },
                    { label: agent.name },
                ]}
                actions={
                    actions.length > 0 ? (
                        <div
                            className="flex flex-wrap gap-2"
                            data-testid="agent-lifecycle-actions"
                        >
                            {actions.map((action) => (
                                <Button
                                    key={action.name}
                                    type="button"
                                    size="sm"
                                    variant={action.primary ? "default" : "outline"}
                                    data-testid={`lifecycle-${action.name}`}
                                    className={
                                        action.destructive
                                            ? "border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                            : undefined
                                    }
                                    onClick={() => {
                                        setActionError(null);
                                        setPendingAction(action);
                                    }}
                                >
                                    {action.label}
                                </Button>
                            ))}
                        </div>
                    ) : null
                }
            />

            <div
                className="text-muted-foreground flex flex-wrap items-center gap-2 text-sm"
                data-testid="agent-meta"
            >
                <LifecycleBadge status={agent.status} />
                <span className="border-border rounded-md border px-1.5 py-0.5 text-xs">
                    {agent.environment}
                </span>
                <span className="border-border rounded-md border px-1.5 py-0.5 text-xs capitalize">
                    {agent.risk_tier}
                </span>
                <span className="border-border rounded-md border px-1.5 py-0.5 text-xs capitalize">
                    {agent.autonomy}
                </span>
                {permissive.length > 0 ? (
                    <span
                        className="border-status-running/40 bg-status-running-bg text-status-running-fg rounded-md border px-1.5 py-0.5 text-xs"
                        data-testid="permissive-chip"
                    >
                        permissive
                    </span>
                ) : null}
                {ownerName ? <span>Owner {ownerName}</span> : null}
                <span>
                    Last seen{" "}
                    {agent.last_seen_at ? (
                        <RelativeTime value={agent.last_seen_at} />
                    ) : (
                        "never"
                    )}
                </span>
            </div>

            {actionError ? (
                <p
                    className="text-status-error-fg text-sm"
                    role="alert"
                    data-testid="lifecycle-error"
                >
                    {actionError}
                </p>
            ) : null}

            <nav
                className="border-border flex flex-wrap gap-1 border-b"
                aria-label="Agent"
                data-testid="agent-tabs"
            >
                {AGENT_DETAIL_TABS.map((id) => (
                    <button
                        key={id}
                        type="button"
                        data-testid={`agent-tab-${id}`}
                        className={cn(
                            "text-muted-foreground hover:text-foreground -mb-px border-b-2 border-transparent px-3 py-2 text-sm capitalize",
                            tab === id && "text-foreground border-primary font-medium",
                        )}
                        onClick={() => setTab(id)}
                    >
                        {id}
                    </button>
                ))}
            </nav>

            {tab === "overview" ? (
                <AgentOverview
                    agent={agent}
                    owners={ownersQuery.data?.items ?? []}
                    denials24h={footprintQuery.data?.denials ?? 0}
                    toolCalls24h={footprintQuery.data?.tool_calls ?? 0}
                    cost24h={footprintQuery.data?.cost_usd ?? 0}
                    pendingApprovals={pendingQuery.data ?? 0}
                    posture={postureQuery.data ?? null}
                    permissive={permissive}
                    runs={runsQuery.data?.items ?? []}
                    agentNames={agentNames}
                    loading={runsQuery.isPending}
                />
            ) : null}

            {tab === "activity" ? (
                <AgentActivity
                    agentName={agent.name}
                    search={search}
                    onSearchChange={(next) => {
                        void navigate({
                            search: () => ({
                                ...next,
                                agent: agent.name,
                                section: "activity",
                            }),
                            replace: true,
                        });
                    }}
                />
            ) : null}

            {tab === "traces" ? (
                <AgentTraces agentName={agent.name} agentNames={agentNames} />
            ) : null}

            {tab === "access" ? <AgentAccess agentId={agent.id} /> : null}

            {tab === "instances" ? <AgentInstances agentId={agent.id} /> : null}

            {tab === "policy" ? (
                <Suspense
                    fallback={
                        <p className="text-muted-foreground text-sm">Loading editor…</p>
                    }
                >
                    <AgentPolicy
                        agentId={agent.id}
                        storedYaml={policyQuery.data ?? ""}
                    />
                </Suspense>
            ) : null}

            {tab === "connect" ? <AgentConnect agentId={agent.id} /> : null}

            <ConfirmDialog
                open={pendingAction != null}
                title={pendingAction?.label ?? "Confirm"}
                sentence={pendingAction?.sentence ?? ""}
                confirmLabel={pendingAction?.label ?? "Confirm"}
                reasonRequired={false}
                destructive={Boolean(pendingAction?.destructive)}
                onCancel={() => {
                    setPendingAction(null);
                    setActionError(null);
                }}
                onConfirm={(reason) => {
                    if (!pendingAction) {
                        return;
                    }
                    setActionError(null);
                    lifecycleMutation.mutate({
                        action: pendingAction.name,
                        reason,
                    });
                }}
            />

            <p className="text-muted-foreground text-xs">
                <Link
                    to="/agents"
                    search={(prev) => prev}
                    className="underline-offset-2 hover:underline"
                >
                    Back to agents
                </Link>
            </p>
        </div>
    );
}
