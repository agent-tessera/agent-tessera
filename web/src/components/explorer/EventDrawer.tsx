import { Link } from "@tanstack/react-router";
import { useCallback, useState } from "react";

import type { EventItem } from "@/api/events";
import { Drawer } from "@/components/Drawer";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { JsonView } from "@/components/JsonView";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { StatusBadge, type StatusKind } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { setFilterValue, type ExplorerSearch } from "@/lib/explorer-search";

export type EventDrawerProps = {
    event: EventItem | null;
    open: boolean;
    onClose: () => void;
    search: ExplorerSearch;
    onSearchChange: (next: ExplorerSearch) => void;
};

const EFFECTS = new Set<EffectKind>(["allow", "deny", "require_approval"]);
const STATUSES = new Set<StatusKind>(["ok", "error", "revoked", "running"]);

async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

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
 * Event detail drawer: summary, full JSON, filter/copy/trace actions.
 */
export function EventDrawer({
    event,
    open,
    onClose,
    search,
    onSearchChange,
}: EventDrawerProps) {
    const [copied, setCopied] = useState<"json" | "link" | null>(null);

    const markCopied = useCallback((kind: "json" | "link") => {
        setCopied(kind);
        window.setTimeout(() => setCopied(null), 1200);
    }, []);

    if (!event) {
        return null;
    }

    const agent =
        (typeof event.agent_name === "string" && event.agent_name) ||
        (typeof event.agent === "string" && event.agent) ||
        "";
    const resource = typeof event.resource === "string" ? event.resource : "";
    const rule = typeof event.rule === "string" ? event.rule : "";
    const run = typeof event.run === "string" ? event.run : "";
    const spanId = typeof event.span_id === "string" ? event.span_id : "";
    const effect = asEffect(event.effect);
    const status = asStatus(event.status);
    const title =
        typeof event.type === "string" && event.type
            ? `${event.type} · ${event.id.slice(0, 8)}`
            : event.id;

    return (
        <Drawer
            open={open}
            title={title}
            onClose={onClose}
            aria-label="Event detail"
            footer={
                <div className="flex flex-wrap gap-2">
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="explorer-drawer-copy-json"
                        onClick={() => {
                            void copyText(JSON.stringify(event, null, 2)).then((ok) => {
                                if (ok) {
                                    markCopied("json");
                                }
                            });
                        }}
                    >
                        {copied === "json" ? "Copied JSON" : "Copy as JSON"}
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="explorer-drawer-copy-link"
                        onClick={() => {
                            void copyText(window.location.href).then((ok) => {
                                if (ok) {
                                    markCopied("link");
                                }
                            });
                        }}
                    >
                        {copied === "link" ? "Copied link" : "Copy link"}
                    </Button>
                </div>
            }
        >
            <div className="space-y-4" data-testid="explorer-drawer">
                <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
                    <dt className="text-muted-foreground">Time</dt>
                    <dd>
                        {event.ts ? <RelativeTime value={String(event.ts)} /> : "—"}
                    </dd>
                    <dt className="text-muted-foreground">Agent</dt>
                    <dd>{agent || "—"}</dd>
                    <dt className="text-muted-foreground">Type</dt>
                    <dd>{typeof event.type === "string" ? event.type : "—"}</dd>
                    <dt className="text-muted-foreground">Kind</dt>
                    <dd>{typeof event.kind === "string" ? event.kind : "—"}</dd>
                    <dt className="text-muted-foreground">Resource</dt>
                    <dd>{resource ? <Mono>{resource}</Mono> : "—"}</dd>
                    <dt className="text-muted-foreground">Effect</dt>
                    <dd>{effect ? <EffectBadge effect={effect} /> : "—"}</dd>
                    <dt className="text-muted-foreground">Status</dt>
                    <dd>{status ? <StatusBadge status={status} /> : "—"}</dd>
                    <dt className="text-muted-foreground">Rule</dt>
                    <dd>{rule ? <Mono>{rule}</Mono> : "—"}</dd>
                    <dt className="text-muted-foreground">Run</dt>
                    <dd>{run ? <Mono>{run}</Mono> : "—"}</dd>
                    <dt className="text-muted-foreground">Span</dt>
                    <dd>{spanId ? <Mono>{spanId}</Mono> : "—"}</dd>
                </dl>

                <div className="flex flex-wrap gap-2">
                    {run && spanId ? (
                        <Button type="button" size="sm" variant="outline" asChild>
                            <Link
                                to="/traces/$runId"
                                params={{ runId: run }}
                                search={{ span: spanId }}
                                data-testid="explorer-drawer-open-trace"
                            >
                                Open trace
                            </Link>
                        </Button>
                    ) : null}
                    {agent ? (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="explorer-drawer-filter-agent"
                            onClick={() => {
                                onSearchChange(setFilterValue(search, "agent", agent));
                                onClose();
                            }}
                        >
                            Filter to this agent
                        </Button>
                    ) : null}
                    {resource ? (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="explorer-drawer-filter-resource"
                            onClick={() => {
                                onSearchChange(
                                    setFilterValue(search, "resource", resource),
                                );
                                onClose();
                            }}
                        >
                            Filter to this resource
                        </Button>
                    ) : null}
                    {rule ? (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="explorer-drawer-filter-rule"
                            onClick={() => {
                                onSearchChange(setFilterValue(search, "rule", rule));
                                onClose();
                            }}
                        >
                            Filter to this rule
                        </Button>
                    ) : null}
                </div>

                <JsonView value={event} />
            </div>
        </Drawer>
    );
}
