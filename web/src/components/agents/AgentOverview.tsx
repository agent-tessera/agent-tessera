import { Link } from "@tanstack/react-router";

import type { Agent, AgentPosture, Owner } from "@/api/agents";
import type { RunListItem } from "@/api/runs";
import { AgentMetadataForm } from "@/components/agents/AgentMetadataForm";
import { EmptyState } from "@/components/EmptyState";
import { RecentRuns } from "@/components/overview/RecentRuns";
import { formatMoney } from "@/lib/money";
import { postureHint } from "@/lib/posture-hint";
import { cn } from "@/lib/utils";

export type AgentOverviewProps = {
    agent: Agent;
    owners: Owner[];
    denials24h: number;
    toolCalls24h: number;
    cost24h: number;
    pendingApprovals: number;
    posture: AgentPosture | null;
    permissive: string[];
    runs: RunListItem[];
    agentNames: Record<string, string>;
    loading?: boolean;
};

/** Overview tab: L5 tiles, posture card, metadata editor, recent runs. */
export function AgentOverview({
    agent,
    owners,
    denials24h,
    toolCalls24h,
    cost24h,
    pendingApprovals,
    posture,
    permissive,
    runs,
    agentNames,
    loading,
}: AgentOverviewProps) {
    return (
        <div className="space-y-6" data-testid="agent-overview">
            {agent.orphaned ? (
                <p
                    className="border-status-error/40 bg-status-error-bg text-status-error-fg rounded-md border px-3 py-2 text-sm"
                    role="alert"
                    data-testid="agent-orphaned-banner"
                >
                    This agent is orphaned: its owner is inactive.
                </p>
            ) : null}

            {permissive.length > 0 ? (
                <p
                    className="border-status-running/40 bg-status-running-bg text-status-running-fg rounded-md border px-3 py-2 text-sm"
                    role="status"
                    data-testid="agent-permissive-warning"
                >
                    This policy allows every {permissive.join(", ")}. Narrow it before
                    production.
                </p>
            ) : null}

            <ul
                className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
                data-testid="agent-overview-tiles"
            >
                <Tile
                    testId="denials-24h"
                    value={String(denials24h)}
                    label="Denials 24 h"
                />
                <Tile
                    testId="tools-24h"
                    value={String(toolCalls24h)}
                    label="Tool calls 24 h"
                />
                <Tile
                    testId="cost-24h"
                    value={formatMoney(cost24h)}
                    label="Cost 24 h"
                />
                <Tile
                    testId="pending"
                    value={String(pendingApprovals)}
                    label="Pending approvals"
                />
            </ul>

            <section
                className="border-border bg-panel space-y-3 rounded-md border p-4"
                data-testid="agent-posture-card"
                aria-labelledby="agent-posture-heading"
            >
                <h2
                    id="agent-posture-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    Posture
                </h2>
                {posture ? (
                    <>
                        <ul className="grid gap-3 sm:grid-cols-2">
                            <Tile
                                testId="posture-score"
                                value={String(Math.round(posture.score))}
                                label="Score"
                                compact
                            />
                            <Tile
                                testId="posture-coverage"
                                value={formatCoverage(posture.coverage)}
                                label="Coverage"
                                compact
                            />
                        </ul>
                        <ul
                            className="text-muted-foreground grid gap-1 text-sm sm:grid-cols-2"
                            data-testid="posture-components"
                        >
                            <li data-testid="posture-owner">
                                Owner {posture.components.owner} / 20
                            </li>
                            <li data-testid="posture-declared">
                                Declared {posture.components.guardrails_declared} / 20
                            </li>
                            <li data-testid="posture-coverage-points">
                                Coverage points{" "}
                                {formatPoints(posture.components.coverage)} / 30
                            </li>
                            <li data-testid="posture-denial">
                                Denial rate{" "}
                                {formatPoints(posture.components.denial_rate)} / 15
                            </li>
                            <li data-testid="posture-ungoverned">
                                Ungoverned {posture.components.ungoverned} / 15
                            </li>
                            <li data-testid="posture-probes">
                                Probes {posture.components.probes} / 0
                            </li>
                        </ul>
                        <p className="text-muted-foreground text-xs">
                            {postureHint(posture)}
                        </p>
                    </>
                ) : (
                    <EmptyState
                        title="No posture yet"
                        body="Scores appear after this agent has a policy and starts reporting events."
                    />
                )}
            </section>

            <AgentMetadataForm agent={agent} owners={owners} />

            <RecentRuns runs={runs} agentNames={agentNames} loading={loading} />
            <p className="text-muted-foreground text-xs">
                <Link
                    to="/traces"
                    search={{ agent: agent.name }}
                    className="underline-offset-2 hover:underline"
                >
                    View all traces for this agent
                </Link>
            </p>
        </div>
    );
}

function Tile({
    value,
    label,
    testId,
    compact,
}: {
    value: string;
    label: string;
    testId: string;
    compact?: boolean;
}) {
    return (
        <li
            className={cn(
                "border-border bg-panel rounded-md border p-3",
                compact && "border-0 bg-transparent p-0",
            )}
            data-testid={testId}
        >
            <div className="text-foreground text-2xl font-semibold tracking-tight tabular-nums">
                {value}
            </div>
            <div className="text-muted-foreground text-xs tracking-wide uppercase">
                {label}
            </div>
        </li>
    );
}

function formatCoverage(value: number): string {
    if (!Number.isFinite(value)) {
        return "0";
    }
    if (value === 0 || value === 1) {
        return String(value);
    }
    return value.toFixed(2);
}

function formatPoints(value: number): string {
    if (!Number.isFinite(value)) {
        return "0";
    }
    if (Number.isInteger(value)) {
        return String(value);
    }
    return value.toFixed(1);
}
