import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";

import { settingsKeys, auditKeys } from "@/api/keys";
import { fetchAuditLog, fetchAuditVerify } from "@/api/overview";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Audit settings: chain verify status and hash-chained log rows.
 */
export function AuditSettings() {
    const verifyQuery = useQuery({
        queryKey: auditKeys.verify(),
        queryFn: fetchAuditVerify,
    });

    const logQuery = useInfiniteQuery({
        queryKey: settingsKeys.auditLog({ limit: 50 }),
        queryFn: ({ pageParam }) => fetchAuditLog({ limit: 50, cursor: pageParam }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    if (verifyQuery.isError && logQuery.isError) {
        return (
            <ErrorState
                title="Could not load audit"
                message={
                    verifyQuery.error instanceof Error
                        ? verifyQuery.error.message
                        : "Request failed"
                }
                onRetry={() => {
                    void verifyQuery.refetch();
                    void logQuery.refetch();
                }}
            />
        );
    }

    const items = logQuery.data?.pages.flatMap((page) => page.items) ?? [];
    const verifyBroken = verifyQuery.data?.ok === false;

    return (
        <div className="space-y-8" data-testid="settings-audit">
            <section
                className={cn(
                    "rounded-md border p-4",
                    verifyBroken
                        ? "border-status-error/40 bg-status-error-bg"
                        : "border-border bg-panel",
                )}
                data-testid="audit-verify-banner"
                aria-labelledby="audit-verify-heading"
            >
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex items-start gap-3">
                        {verifyBroken ? (
                            <ShieldAlert
                                className="text-status-error-fg mt-0.5 size-5 shrink-0"
                                aria-hidden
                            />
                        ) : (
                            <ShieldCheck
                                className="text-status-ok-fg mt-0.5 size-5 shrink-0"
                                aria-hidden
                            />
                        )}
                        <div>
                            <h2
                                id="audit-verify-heading"
                                className="text-foreground text-sm font-semibold"
                            >
                                Chain verification
                            </h2>
                            {verifyQuery.isPending ? (
                                <p className="text-muted-foreground mt-1 text-sm">
                                    Checking…
                                </p>
                            ) : null}
                            {verifyQuery.isError ? (
                                <p
                                    className="text-status-error-fg mt-1 text-sm"
                                    role="alert"
                                >
                                    {verifyQuery.error instanceof Error
                                        ? verifyQuery.error.message
                                        : "Verify failed"}
                                </p>
                            ) : null}
                            {verifyQuery.data ? (
                                <div className="mt-1 space-y-1 text-sm">
                                    {verifyQuery.data.ok ? (
                                        <p role="status" data-testid="audit-verify-ok">
                                            Intact · {verifyQuery.data.events} events
                                        </p>
                                    ) : (
                                        <p
                                            className="text-status-error-fg"
                                            role="alert"
                                            data-testid="audit-verify-fail"
                                        >
                                            Failed
                                            {verifyQuery.data.error_seq != null
                                                ? ` at seq ${verifyQuery.data.error_seq}`
                                                : ""}
                                            {verifyQuery.data.error
                                                ? `: ${verifyQuery.data.error}`
                                                : ""}
                                        </p>
                                    )}
                                </div>
                            ) : null}
                        </div>
                    </div>
                    {verifyQuery.data ? (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="audit-verify-refresh"
                            onClick={() => void verifyQuery.refetch()}
                        >
                            Re-verify
                        </Button>
                    ) : null}
                </div>
            </section>

            <section className="space-y-3" aria-labelledby="audit-log-heading">
                <h2
                    id="audit-log-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    Audit chain
                </h2>
                {logQuery.isError ? (
                    <ErrorState
                        title="Could not load audit log"
                        message={
                            logQuery.error instanceof Error
                                ? logQuery.error.message
                                : "Request failed"
                        }
                        onRetry={() => void logQuery.refetch()}
                    />
                ) : null}
                {logQuery.isPending ? (
                    <p className="text-muted-foreground text-sm">Loading…</p>
                ) : null}
                {!logQuery.isPending && items.length === 0 ? (
                    <EmptyState
                        title="No audit rows"
                        body="Control-plane actions append hash-chained rows here."
                        className="border-border bg-panel rounded-md border py-8"
                    />
                ) : null}
                {items.length > 0 ? (
                    <ul
                        className="divide-border border-border bg-panel divide-y rounded-md border"
                        data-testid="audit-log-list"
                    >
                        {items.map((row) => (
                            <li
                                key={row.seq}
                                className="flex items-start justify-between gap-3 px-3 py-2.5"
                                data-testid={`audit-row-${row.seq}`}
                            >
                                <div className="min-w-0 space-y-0.5">
                                    <div className="flex flex-wrap items-baseline gap-2">
                                        <span className="text-muted-foreground font-mono text-xs tabular-nums">
                                            #{row.seq}
                                        </span>
                                        <span className="text-foreground text-sm font-medium">
                                            {row.type}
                                        </span>
                                    </div>
                                    <p className="text-muted-foreground text-xs">
                                        Actor {row.actor}
                                    </p>
                                    <p className="text-muted-foreground truncate font-mono text-[11px]">
                                        <Mono>{row.hash.slice(0, 16)}…</Mono>
                                    </p>
                                </div>
                                <RelativeTime
                                    value={row.ts}
                                    className="text-muted-foreground shrink-0 text-xs"
                                />
                            </li>
                        ))}
                    </ul>
                ) : null}
                {logQuery.hasNextPage ? (
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={logQuery.isFetchingNextPage}
                        data-testid="audit-log-more"
                        onClick={() => void logQuery.fetchNextPage()}
                    >
                        {logQuery.isFetchingNextPage ? "Loading…" : "Load more"}
                    </Button>
                ) : null}
            </section>
        </div>
    );
}
