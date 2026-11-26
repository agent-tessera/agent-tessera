import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Inbox } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchAgents } from "@/api/agents";
import {
    approveApproval,
    denyApproval,
    fetchApproval,
    fetchApprovals,
    type Approval,
} from "@/api/approvals";
import { TesseraApiError } from "@/api/client";
import { agentsKeys, approvalsKeys } from "@/api/keys";
import { ApprovalDetail } from "@/components/approvals/ApprovalDetail";
import { ApprovalsList } from "@/components/approvals/ApprovalsList";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { setPageTitle } from "@/lib/page-title";

function focusApprovalComment(): void {
    document
        .querySelector<HTMLTextAreaElement>('[data-testid="approval-comment"]')
        ?.focus();
}

export type ApprovalsSearch = {
    status?: string;
    id?: string;
    agent_id?: string;
    environment?: string;
};

const STATUS_OPTIONS = [
    { value: "pending", label: "Pending" },
    { value: "approved", label: "Approved" },
    { value: "denied", label: "Denied" },
    { value: "timeout", label: "Timeout" },
    { value: "cancelled", label: "Cancelled" },
    { value: "all", label: "All" },
] as const;

function parseApprovalsSearch(search: Record<string, unknown>): ApprovalsSearch {
    const out: ApprovalsSearch = {};
    for (const key of ["status", "id", "agent_id", "environment"] as const) {
        const value = search[key];
        if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    return out;
}

export const Route = createFileRoute("/_console/approvals")({
    validateSearch: parseApprovalsSearch,
    component: ApprovalsPage,
});

const PAGE_SIZE = 50;

function isEditableTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) {
        return false;
    }
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return true;
    }
    return target.isContentEditable;
}

/**
 * Approvals inbox: list left, detail right, keyboard j/k/a/d/Enter.
 */
function ApprovalsPage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const queryClient = useQueryClient();
    const [decideError, setDecideError] = useState<string | null>(null);
    const [denyOpen, setDenyOpen] = useState(false);
    const [denyComment, setDenyComment] = useState("");

    useEffect(() => {
        setPageTitle("Approvals");
    }, []);

    const statusFilter = search.status ?? "pending";
    const apiStatus = statusFilter === "all" ? undefined : statusFilter;

    const patchSearch = (patch: Partial<ApprovalsSearch>) => {
        void navigate({
            search: (prev) => {
                const next: ApprovalsSearch = { ...prev, ...patch };
                for (const key of Object.keys(patch) as Array<keyof ApprovalsSearch>) {
                    const value = patch[key];
                    if (value === undefined || value === "") {
                        delete next[key];
                    }
                }
                return next;
            },
            replace: true,
        });
    };

    const listQuery = useQuery({
        queryKey: approvalsKeys.list({
            status: apiStatus ?? "all",
            agent_id: search.agent_id,
            limit: PAGE_SIZE,
            environment: search.environment,
        }),
        queryFn: () =>
            fetchApprovals({
                status: apiStatus,
                agent_id: search.agent_id,
                limit: PAGE_SIZE,
            }),
        refetchInterval: statusFilter === "pending" ? 10_000 : false,
    });

    const agentsQuery = useQuery({
        queryKey: agentsKeys.list({ limit: 200, environment: search.environment }),
        queryFn: () =>
            fetchAgents({
                limit: 200,
                environment: search.environment,
            }),
    });

    const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data?.items]);
    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of agentsQuery.data?.items ?? []) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [agentsQuery.data?.items]);

    const selectedId = useMemo(() => {
        if (search.id && items.some((item) => item.id === search.id)) {
            return search.id;
        }
        if (search.id && items.length === 0) {
            return search.id;
        }
        return items[0]?.id;
    }, [items, search.id]);

    const selectedFromList = items.find((item) => item.id === selectedId);

    const detailQuery = useQuery({
        queryKey: approvalsKeys.detail(search.id ?? ""),
        queryFn: () => fetchApproval(search.id!),
        enabled: Boolean(search.id) && selectedFromList == null,
    });

    const selected: Approval | undefined = selectedFromList ?? detailQuery.data;

    useEffect(() => {
        if (!search.id && items[0]) {
            patchSearch({ id: items[0].id });
        }
        // Only sync when the list loads without a selection.
        // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional one-way sync
    }, [items, search.id]);

    const invalidateApprovals = async () => {
        await queryClient.invalidateQueries({ queryKey: approvalsKeys.all });
    };

    const approveMutation = useMutation({
        mutationFn: (input: { id: string; comment: string }) =>
            approveApproval(input.id, { comment: input.comment || null }),
        onSuccess: async (updated) => {
            setDecideError(null);
            await invalidateApprovals();
            const remaining = items.filter(
                (item) => item.id !== updated.id && item.status === "pending",
            );
            if (statusFilter === "pending") {
                patchSearch({ id: remaining[0]?.id });
            } else {
                patchSearch({ id: updated.id });
            }
        },
        onError: (err: unknown) => {
            setDecideError(
                err instanceof TesseraApiError ? err.message : "Approve failed",
            );
        },
    });

    const denyMutation = useMutation({
        mutationFn: (input: { id: string; comment: string }) =>
            denyApproval(input.id, { comment: input.comment || null }),
        onSuccess: async (updated) => {
            setDecideError(null);
            setDenyOpen(false);
            await invalidateApprovals();
            const remaining = items.filter(
                (item) => item.id !== updated.id && item.status === "pending",
            );
            if (statusFilter === "pending") {
                patchSearch({ id: remaining[0]?.id });
            } else {
                patchSearch({ id: updated.id });
            }
        },
        onError: (err: unknown) => {
            setDecideError(
                err instanceof TesseraApiError ? err.message : "Deny failed",
            );
            setDenyOpen(false);
        },
    });

    const busy = approveMutation.isPending || denyMutation.isPending;

    const selectByIndex = (index: number) => {
        const item = items[index];
        if (item) {
            patchSearch({ id: item.id });
        }
    };

    const moveSelection = (delta: number) => {
        if (items.length === 0) {
            return;
        }
        const current = items.findIndex((item) => item.id === selectedId);
        const next =
            current < 0
                ? delta > 0
                    ? 0
                    : items.length - 1
                : Math.max(0, Math.min(items.length - 1, current + delta));
        selectByIndex(next);
    };

    const requestApprove = () => {
        if (!selected || selected.status !== "pending" || busy || denyOpen) {
            return;
        }
        const commentEl = document.querySelector<HTMLTextAreaElement>(
            '[data-testid="approval-comment"]',
        );
        const comment = commentEl?.value.trim() ?? "";
        approveMutation.mutate({ id: selected.id, comment });
    };

    const requestDeny = () => {
        if (!selected || selected.status !== "pending" || busy || denyOpen) {
            return;
        }
        const commentEl = document.querySelector<HTMLTextAreaElement>(
            '[data-testid="approval-comment"]',
        );
        setDenyComment(commentEl?.value.trim() ?? "");
        setDenyOpen(true);
    };

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.defaultPrevented) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.altKey) {
                return;
            }
            if (denyOpen) {
                return;
            }
            if (isEditableTarget(event.target)) {
                return;
            }
            const key = event.key;
            if (key === "j" || key === "J") {
                event.preventDefault();
                moveSelection(1);
                return;
            }
            if (key === "k" || key === "K") {
                event.preventDefault();
                moveSelection(-1);
                return;
            }
            if (key === "a" || key === "A") {
                event.preventDefault();
                requestApprove();
                return;
            }
            if (key === "d" || key === "D") {
                event.preventDefault();
                requestDeny();
                return;
            }
            if (key === "Enter") {
                event.preventDefault();
                if (selectedId) {
                    patchSearch({ id: selectedId });
                    focusApprovalComment();
                }
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- handlers close over latest state
    }, [items, selectedId, selected, busy, denyOpen, statusFilter]);

    const agentOptions = agentsQuery.data?.items ?? [];

    return (
        <div
            className="flex h-full min-h-0 flex-col gap-4"
            data-testid="approvals-inbox"
        >
            <PageHeader
                title="Approvals"
                description="Pending and resolved approval requests."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Approvals" },
                ]}
            />

            <div
                className="flex flex-wrap items-end gap-3"
                data-testid="approvals-filters"
            >
                <label className="space-y-1 text-sm">
                    <span className="text-muted-foreground text-xs tracking-wide uppercase">
                        Status
                    </span>
                    <select
                        className="border-border bg-background focus-visible:ring-ring block rounded-md border px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:outline-none"
                        value={statusFilter}
                        data-testid="approvals-status-filter"
                        onChange={(event) => {
                            const value = event.target.value;
                            patchSearch({
                                status: value === "pending" ? undefined : value,
                                id: undefined,
                            });
                        }}
                    >
                        {STATUS_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                                {opt.label}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="space-y-1 text-sm">
                    <span className="text-muted-foreground text-xs tracking-wide uppercase">
                        Agent
                    </span>
                    <select
                        className="border-border bg-background focus-visible:ring-ring block min-w-40 rounded-md border px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:outline-none"
                        value={search.agent_id ?? ""}
                        data-testid="approvals-agent-filter"
                        onChange={(event) => {
                            const value = event.target.value;
                            patchSearch({
                                agent_id: value || undefined,
                                id: undefined,
                            });
                        }}
                    >
                        <option value="">All agents</option>
                        {agentOptions.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                                {agent.name}
                            </option>
                        ))}
                    </select>
                </label>
                <p className="text-muted-foreground pb-1.5 text-xs">
                    Shortcuts: <kbd className="font-mono">j</kbd>/
                    <kbd className="font-mono">k</kbd> move,{" "}
                    <kbd className="font-mono">a</kbd> approve,{" "}
                    <kbd className="font-mono">d</kbd> deny,{" "}
                    <kbd className="font-mono">Enter</kbd> comment
                </p>
            </div>

            {listQuery.isError ? (
                <ErrorState
                    title="Could not load approvals"
                    message={
                        listQuery.error instanceof Error
                            ? listQuery.error.message
                            : "Request failed"
                    }
                    onRetry={() => void listQuery.refetch()}
                />
            ) : (
                <div
                    className="border-border grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-md border md:grid-cols-[minmax(16rem,22rem)_1fr]"
                    data-testid="approvals-split"
                >
                    <div className="border-border min-h-0 overflow-auto md:border-r">
                        {listQuery.isLoading ? (
                            <p className="text-muted-foreground p-4 text-sm">
                                Loading…
                            </p>
                        ) : (
                            <ApprovalsList
                                items={items}
                                selectedId={selectedId}
                                agentNames={agentNames}
                                onSelect={(id) => patchSearch({ id })}
                            />
                        )}
                    </div>
                    <div className="min-h-0 overflow-auto p-4">
                        {selected ? (
                            <ApprovalDetail
                                approval={selected}
                                agentName={agentNames[selected.agent_id]}
                                busy={busy}
                                error={decideError}
                                onApprove={(comment) =>
                                    approveMutation.mutate({ id: selected.id, comment })
                                }
                                onDeny={(comment) => {
                                    setDenyComment(comment);
                                    setDenyOpen(true);
                                }}
                            />
                        ) : detailQuery.isLoading ? (
                            <p className="text-muted-foreground text-sm">Loading…</p>
                        ) : detailQuery.isError ? (
                            <ErrorState
                                title="Approval not found"
                                message="It may have been resolved or the id is wrong."
                            />
                        ) : (
                            <EmptyState
                                icon={Inbox}
                                title="Select an approval"
                                body="Choose a request from the list to review and decide."
                            />
                        )}
                    </div>
                </div>
            )}

            <ConfirmDialog
                open={denyOpen}
                title="Deny approval"
                sentence="Deny this approval request? The agent will treat the action as rejected."
                confirmLabel="Deny"
                destructive
                reasonRequired={false}
                reasonLabel="Comment"
                onCancel={() => setDenyOpen(false)}
                onConfirm={(reason) => {
                    if (!selected) {
                        setDenyOpen(false);
                        return;
                    }
                    denyMutation.mutate({
                        id: selected.id,
                        comment: reason || denyComment,
                    });
                }}
            />
        </div>
    );
}
