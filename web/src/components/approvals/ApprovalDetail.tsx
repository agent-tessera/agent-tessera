import { Link } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";

import {
    approvalArgsBytes,
    formatApprovalWhy,
    waitingSpanId,
    type Approval,
} from "@/api/approvals";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ApprovalDetailProps = {
    approval: Approval;
    agentName?: string;
    busy?: boolean;
    error?: string | null;
    onApprove: (comment: string) => void;
    onDeny: (comment: string) => void;
};

const EFFECTS = new Set<EffectKind>(["allow", "deny", "require_approval"]);

/**
 * Right-hand approval detail with why, route, args size, and decide form.
 */
export function ApprovalDetail({
    approval,
    agentName,
    busy = false,
    error,
    onApprove,
    onDeny,
}: ApprovalDetailProps) {
    const commentId = useId();
    const commentRef = useRef<HTMLTextAreaElement>(null);
    const [comment, setComment] = useState("");

    useEffect(() => {
        setComment("");
    }, [approval.id]);

    const resource =
        typeof approval.action?.resource === "string" ? approval.action.resource : "—";
    const why = formatApprovalWhy(approval.decision) || "—";
    const effectRaw =
        typeof approval.decision?.effect === "string" ? approval.decision.effect : "";
    const effect = EFFECTS.has(effectRaw as EffectKind)
        ? (effectRaw as EffectKind)
        : null;
    const parties = Array.isArray(approval.route?.parties)
        ? approval.route.parties.filter((p): p is string => typeof p === "string")
        : [];
    const argsBytes = approvalArgsBytes(approval.action);
    const span = waitingSpanId(approval);
    const pending = approval.status === "pending";

    return (
        <div
            className="space-y-6"
            data-testid="approval-detail"
            data-approval-id={approval.id}
        >
            <header className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold">
                        {agentName || approval.agent_id}
                    </h2>
                    <span
                        className="border-border rounded-md border px-1.5 py-0.5 text-xs capitalize"
                        data-testid="approval-status"
                    >
                        {approval.status}
                    </span>
                    {effect ? <EffectBadge effect={effect} /> : null}
                </div>
                <p className="text-sm">
                    Resource <Mono className="text-xs">{resource}</Mono>
                </p>
            </header>

            <section className="space-y-1" data-testid="approval-why">
                <h3 className="text-muted-foreground text-xs tracking-wide uppercase">
                    Why
                </h3>
                <p className="text-sm">{why}</p>
            </section>

            <section className="space-y-2 text-sm">
                <h3 className="text-muted-foreground text-xs tracking-wide uppercase">
                    Context
                </h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
                    <dt className="text-muted-foreground">Principal</dt>
                    <dd>{approval.requested_by_principal || "—"}</dd>
                    <dt className="text-muted-foreground">Run</dt>
                    <dd>
                        {approval.run_id ? (
                            <Button type="button" size="sm" variant="outline" asChild>
                                <Link
                                    to="/traces/$runId"
                                    params={{ runId: approval.run_id }}
                                    search={span ? { span } : {}}
                                    data-testid="approval-run-link"
                                >
                                    <Mono className="text-xs">{approval.run_id}</Mono>
                                </Link>
                            </Button>
                        ) : (
                            "—"
                        )}
                    </dd>
                    <dt className="text-muted-foreground">Created</dt>
                    <dd>
                        <RelativeTime value={approval.created_at} />
                    </dd>
                    <dt className="text-muted-foreground">Expires</dt>
                    <dd>
                        <RelativeTime value={approval.expires_at} />
                    </dd>
                    {approval.resolved_by ? (
                        <>
                            <dt className="text-muted-foreground">Resolved by</dt>
                            <dd>{approval.resolved_by}</dd>
                        </>
                    ) : null}
                </dl>
            </section>

            <section className="space-y-1" data-testid="approval-args">
                <h3 className="text-muted-foreground text-xs tracking-wide uppercase">
                    Arguments
                </h3>
                <p className="text-sm tabular-nums">{argsBytes} bytes</p>
            </section>

            <section className="space-y-2" data-testid="approval-route">
                <h3 className="text-muted-foreground text-xs tracking-wide uppercase">
                    Route
                </h3>
                {parties.length === 0 ? (
                    <p className="text-muted-foreground text-sm">No parties</p>
                ) : (
                    <ol className="space-y-1">
                        {parties.map((party, index) => {
                            const current = index === approval.route_index;
                            return (
                                <li
                                    key={`${party}-${index}`}
                                    className={cn(
                                        "border-border rounded-md border px-2 py-1.5 text-sm",
                                        current && "bg-muted border-primary/40",
                                    )}
                                    data-current={current ? "true" : "false"}
                                >
                                    <span className="font-mono text-xs">{party}</span>
                                    {current ? (
                                        <span className="text-muted-foreground ml-2 text-xs">
                                            (current)
                                        </span>
                                    ) : null}
                                </li>
                            );
                        })}
                    </ol>
                )}
            </section>

            {pending ? (
                <section className="space-y-3" data-testid="approval-decide">
                    <h3 className="text-muted-foreground text-xs tracking-wide uppercase">
                        Decide
                    </h3>
                    <div className="space-y-1.5">
                        <label
                            htmlFor={commentId}
                            className="text-muted-foreground text-xs tracking-wide uppercase"
                        >
                            Comment (optional)
                        </label>
                        <textarea
                            ref={commentRef}
                            id={commentId}
                            name="comment"
                            rows={3}
                            maxLength={1000}
                            value={comment}
                            disabled={busy}
                            onChange={(event) => setComment(event.target.value)}
                            className="border-border bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
                            data-testid="approval-comment"
                        />
                    </div>
                    {error ? (
                        <p
                            className="text-status-error-fg text-sm"
                            data-testid="approval-decide-error"
                            role="alert"
                        >
                            {error}
                        </p>
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                        <Button
                            type="button"
                            size="sm"
                            disabled={busy}
                            data-testid="approval-approve"
                            onClick={() => onApprove(comment.trim())}
                        >
                            Approve
                        </Button>
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            data-testid="approval-deny"
                            className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                            onClick={() => onDeny(comment.trim())}
                        >
                            Deny
                        </Button>
                    </div>
                </section>
            ) : null}
        </div>
    );
}
