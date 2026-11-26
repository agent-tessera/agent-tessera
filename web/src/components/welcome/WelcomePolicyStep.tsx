import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { activateAgent } from "@/api/agents";
import { TesseraApiError } from "@/api/client";
import { policyTemplateKeys } from "@/api/keys";
import {
    applyPolicyTemplate,
    fetchPolicyTemplates,
    type PolicyTemplate,
} from "@/api/policy";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";
import { fillTemplatePreview } from "@/lib/welcome";
import { cn } from "@/lib/utils";

export type WelcomePolicyStepProps = {
    agentId: string;
    agentName: string;
    environment: string;
    onApplied: () => void;
};

/**
 * Step 2: pick a built-in policy template (card + YAML preview) and apply it.
 *
 * Activates the agent after a successful apply so connect can register.
 */
export function WelcomePolicyStep({
    agentId,
    agentName,
    environment,
    onApplied,
}: WelcomePolicyStepProps) {
    const [selected, setSelected] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const templatesQuery = useQuery({
        queryKey: policyTemplateKeys.list(),
        queryFn: fetchPolicyTemplates,
    });

    const items = useMemo(
        () => templatesQuery.data?.items ?? [],
        [templatesQuery.data?.items],
    );
    const selectedTemplate: PolicyTemplate | null = useMemo(() => {
        if (!selected) {
            return items[0] ?? null;
        }
        return items.find((item) => item.id === selected) ?? null;
    }, [items, selected]);

    const activeId = selectedTemplate?.id ?? null;

    const preview = useMemo(() => {
        if (!selectedTemplate) {
            return "";
        }
        return fillTemplatePreview(selectedTemplate.yaml, agentName, environment);
    }, [selectedTemplate, agentName, environment]);

    const applyMutation = useMutation({
        mutationFn: async (templateId: string) => {
            await applyPolicyTemplate(agentId, templateId);
            await activateAgent(agentId);
        },
        onSuccess: () => {
            setError(null);
            onApplied();
        },
        onError: (err) => {
            setError(
                err instanceof TesseraApiError
                    ? err.message
                    : err instanceof Error
                      ? err.message
                      : "Could not apply template",
            );
        },
    });

    if (templatesQuery.isError) {
        return (
            <ErrorState
                title="Could not load policy templates"
                message={
                    templatesQuery.error instanceof Error
                        ? templatesQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void templatesQuery.refetch()}
            />
        );
    }

    return (
        <div className="space-y-4" data-testid="welcome-policy-step">
            <div className="space-y-1">
                <h2 className="text-sm font-semibold">Choose a policy template</h2>
                <p className="text-muted-foreground text-sm">
                    Templates fill in this agent&apos;s name and environment, then
                    activate it for connections.
                </p>
            </div>

            {templatesQuery.isPending && !items.length ? (
                <p className="text-muted-foreground text-sm">Loading templates…</p>
            ) : (
                <ul
                    className="grid gap-3 sm:grid-cols-3"
                    data-testid="welcome-template-cards"
                >
                    {items.map((item) => {
                        const isActive = item.id === activeId;
                        return (
                            <li key={item.id}>
                                <button
                                    type="button"
                                    data-testid={`welcome-template-${item.id}`}
                                    data-selected={isActive ? "true" : "false"}
                                    className={cn(
                                        "border-border bg-panel hover:bg-muted/40 flex h-full w-full flex-col gap-1 rounded-md border p-3 text-left",
                                        isActive &&
                                            "border-primary ring-primary/20 ring-2",
                                    )}
                                    onClick={() => setSelected(item.id)}
                                >
                                    <span className="text-sm font-medium">
                                        {item.title}
                                    </span>
                                    <span className="text-muted-foreground text-xs">
                                        {item.blurb}
                                    </span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}

            {preview ? (
                <div className="space-y-2">
                    <h3 className="text-sm font-medium">YAML preview</h3>
                    <pre
                        data-testid="welcome-template-preview"
                        className="border-border bg-muted/40 max-h-80 overflow-auto rounded-md border p-3 font-mono text-[12px] whitespace-pre-wrap"
                    >
                        {preview}
                    </pre>
                </div>
            ) : null}

            {error ? (
                <p
                    className="text-status-error text-sm"
                    role="alert"
                    data-testid="welcome-policy-error"
                >
                    {error}
                </p>
            ) : null}

            <Button
                type="button"
                size="sm"
                data-testid="welcome-policy-apply"
                disabled={!activeId || applyMutation.isPending}
                onClick={() => {
                    if (activeId) {
                        applyMutation.mutate(activeId);
                    }
                }}
            >
                {applyMutation.isPending ? "Applying…" : "Apply template"}
            </Button>
        </div>
    );
}
