import { Link } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";

import type { TraceSpan } from "@/api/runs";
import { Drawer } from "@/components/Drawer";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { JsonView } from "@/components/JsonView";
import { Mono } from "@/components/Mono";
import { StatusBadge, type StatusKind } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { formatDuration } from "@/lib/overview-metrics";
import { externalTraceUrl } from "@/lib/trace-url";

export type SpanDrawerProps = {
    span: TraceSpan | null;
    open: boolean;
    onClose: () => void;
    /** Template from `GET /v1/me` (`trace_url`). */
    traceUrlTemplate?: string | null;
    agentName?: string;
};

const EFFECTS = new Set<EffectKind>(["allow", "deny", "require_approval"]);
const STATUSES = new Set<StatusKind>(["ok", "error", "revoked", "running"]);

function asEffect(value: unknown): EffectKind | null {
    if (typeof value !== "string" || !EFFECTS.has(value as EffectKind)) {
        return null;
    }
    return value as EffectKind;
}

function asStatus(value: unknown): StatusKind | null {
    if (typeof value !== "string" || !STATUSES.has(value as StatusKind)) {
        return null;
    }
    return value as StatusKind;
}

/**
 * Span detail drawer: decision, outcome, timings, effects, and external link.
 */
export function SpanDrawer({
    span,
    open,
    onClose,
    traceUrlTemplate,
    agentName,
}: SpanDrawerProps) {
    if (!span) {
        return null;
    }

    const effect = asEffect(span.decision?.effect);
    const status = asStatus(span.status);
    const external = externalTraceUrl(traceUrlTemplate, span.trace_id, span.span_id);
    const title = span.resource
        ? `${span.kind ?? "span"} · ${span.resource}`
        : span.span_id;

    return (
        <Drawer
            open={open}
            title={title}
            onClose={onClose}
            aria-label="Span detail"
            footer={
                external ? (
                    <Button type="button" size="sm" variant="outline" asChild>
                        <a
                            href={external}
                            target="_blank"
                            rel="noreferrer"
                            data-testid="span-external-trace"
                        >
                            Open in tracing tool
                            <ExternalLink className="size-3.5" aria-hidden />
                        </a>
                    </Button>
                ) : null
            }
        >
            <div className="space-y-4" data-testid="span-drawer">
                <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
                    <dt className="text-muted-foreground">Span</dt>
                    <dd>
                        <Mono>{span.span_id}</Mono>
                    </dd>
                    <dt className="text-muted-foreground">Effect</dt>
                    <dd>{effect ? <EffectBadge effect={effect} /> : "—"}</dd>
                    <dt className="text-muted-foreground">Rule</dt>
                    <dd>
                        {span.decision?.rule ? <Mono>{span.decision.rule}</Mono> : "—"}
                    </dd>
                    <dt className="text-muted-foreground">Reason</dt>
                    <dd>
                        {span.decision?.reason ? (
                            <span data-testid="span-decision-reason">
                                {String(span.decision.reason)}
                            </span>
                        ) : (
                            "—"
                        )}
                    </dd>
                    <dt className="text-muted-foreground">Outcome</dt>
                    <dd>
                        {status ? <StatusBadge status={status} /> : "—"}
                        {span.error_type ? (
                            <span className="text-muted-foreground ml-2 text-xs">
                                {span.error_type}
                            </span>
                        ) : null}
                    </dd>
                    <dt className="text-muted-foreground">Duration</dt>
                    <dd className="tabular-nums">
                        {formatDuration(span.duration_ms ?? Number.NaN)}
                    </dd>
                    <dt className="text-muted-foreground">Approval wait</dt>
                    <dd className="tabular-nums">
                        {span.approval_wait_ms != null
                            ? formatDuration(span.approval_wait_ms)
                            : "—"}
                    </dd>
                    <dt className="text-muted-foreground">Start</dt>
                    <dd className="tabular-nums">
                        <time dateTime={span.start}>{span.start}</time>
                    </dd>
                    <dt className="text-muted-foreground">End</dt>
                    <dd className="tabular-nums">
                        {span.end ? <time dateTime={span.end}>{span.end}</time> : "—"}
                    </dd>
                    <dt className="text-muted-foreground">Effects</dt>
                    <dd>
                        {span.effects.length > 0 ? (
                            <ul className="space-y-1" data-testid="span-effects">
                                {span.effects.map((item) => (
                                    <li key={item}>
                                        <Mono>{item}</Mono>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            "—"
                        )}
                    </dd>
                    <dt className="text-muted-foreground">Approval</dt>
                    <dd>
                        {span.approval_id ? (
                            <Button type="button" size="sm" variant="outline" asChild>
                                <Link
                                    to="/approvals"
                                    search={{ id: span.approval_id }}
                                    data-testid="span-approval-link"
                                >
                                    <Mono>{span.approval_id}</Mono>
                                </Link>
                            </Button>
                        ) : (
                            "—"
                        )}
                    </dd>
                    <dt className="text-muted-foreground">Principal</dt>
                    <dd data-testid="span-principal">
                        {span.principal?.trim() ? span.principal : "—"}
                    </dd>
                    <dt className="text-muted-foreground">Agent</dt>
                    <dd>{agentName || span.agent_id || "—"}</dd>
                    <dt className="text-muted-foreground">Instance</dt>
                    <dd>{span.instance_id ? <Mono>{span.instance_id}</Mono> : "—"}</dd>
                    <dt className="text-muted-foreground">Trace id</dt>
                    <dd>{span.trace_id ? <Mono>{span.trace_id}</Mono> : "—"}</dd>
                </dl>

                <div className="space-y-2">
                    <h3 className="text-xs font-semibold tracking-wide uppercase">
                        Raw events
                    </h3>
                    {span.event_ids.length > 0 ? (
                        <ul
                            className="text-muted-foreground space-y-1 text-xs"
                            data-testid="span-event-ids"
                        >
                            {span.event_ids.map((id) => (
                                <li key={id}>
                                    <Mono>{id}</Mono>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="text-muted-foreground text-sm">No events</p>
                    )}
                    <JsonView value={span} />
                </div>
            </div>
        </Drawer>
    );
}
