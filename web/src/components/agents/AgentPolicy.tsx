import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { agentsKeys } from "@/api/keys";
import {
    dryRunAgentPolicy,
    putAgentPolicy,
    validateAgentPolicy,
    type PolicyDryRunKind,
    type PolicyDryRunResult,
} from "@/api/policy";
import { TesseraApiError } from "@/api/client";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { Button } from "@/components/ui/button";
import { permissiveKinds } from "@/lib/permissive-policy";
import { policyErrorDiagnostics, type PolicyLintDiagnostic } from "@/lib/policy-lint";

import { PolicyYamlEditor } from "./PolicyYamlEditor";

export type AgentPolicyProps = {
    agentId: string;
    storedYaml: string;
};

const DRY_KINDS: PolicyDryRunKind[] = ["model", "tool", "data", "agent"];

/**
 * M11 Policy tab: CodeMirror YAML, lint, side-by-side diff, dry-run.
 */
export function AgentPolicy({ agentId, storedYaml }: AgentPolicyProps) {
    const queryClient = useQueryClient();
    const [draft, setDraft] = useState(storedYaml);
    const [diagnostics, setDiagnostics] = useState<PolicyLintDiagnostic[]>([]);
    const [errorText, setErrorText] = useState<string | null>(null);
    const [validateDiff, setValidateDiff] = useState<string | null>(null);
    const [statusMessage, setStatusMessage] = useState<string | null>(null);
    const [dryKind, setDryKind] = useState<PolicyDryRunKind>("tool");
    const [dryResource, setDryResource] = useState("shell.exec");
    const [dryResult, setDryResult] = useState<PolicyDryRunResult | null>(null);
    const [dryError, setDryError] = useState<string | null>(null);

    useEffect(() => {
        setDraft(storedYaml);
        setDiagnostics([]);
        setErrorText(null);
        setValidateDiff(null);
        setStatusMessage(null);
    }, [storedYaml]);

    const dirty = draft !== storedYaml;
    const permissive = useMemo(() => permissiveKinds(draft), [draft]);

    const applyError = (message: string) => {
        setErrorText(message);
        setDiagnostics(policyErrorDiagnostics(message));
        setValidateDiff(null);
        setStatusMessage(null);
    };

    const clearError = () => {
        setErrorText(null);
        setDiagnostics([]);
    };

    const validateMutation = useMutation({
        mutationFn: () => validateAgentPolicy(agentId, draft),
        onSuccess: (result) => {
            if (!result.valid) {
                applyError(result.error ?? "Invalid policy");
                return;
            }
            clearError();
            setValidateDiff(result.diff?.trim() ? result.diff : null);
            setStatusMessage("Policy is valid.");
        },
        onError: (error) => {
            applyError(error instanceof Error ? error.message : "Validate failed");
        },
    });

    const saveMutation = useMutation({
        mutationFn: () => putAgentPolicy(agentId, draft),
        onSuccess: async () => {
            clearError();
            setValidateDiff(null);
            setStatusMessage("Policy saved.");
            await queryClient.invalidateQueries({
                queryKey: ["agents", "policy-yaml", agentId],
            });
            await queryClient.invalidateQueries({
                queryKey: agentsKeys.detail(agentId),
            });
            await queryClient.invalidateQueries({ queryKey: agentsKeys.all });
        },
        onError: (error) => {
            const message =
                error instanceof TesseraApiError
                    ? error.message
                    : error instanceof Error
                      ? error.message
                      : "Save failed";
            applyError(message);
        },
    });

    const dryRunMutation = useMutation({
        mutationFn: () =>
            dryRunAgentPolicy(agentId, {
                kind: dryKind,
                resource: dryResource.trim(),
            }),
        onSuccess: (result) => {
            setDryError(null);
            setDryResult(result);
        },
        onError: (error) => {
            setDryResult(null);
            setDryError(error instanceof Error ? error.message : "Dry run failed");
        },
    });

    return (
        <div className="space-y-6" data-testid="agent-policy">
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

            <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-medium">YAML</h2>
                    <div className="flex flex-wrap gap-2">
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            data-testid="policy-validate"
                            disabled={validateMutation.isPending}
                            onClick={() => validateMutation.mutate()}
                        >
                            Validate
                        </Button>
                        <Button
                            type="button"
                            size="sm"
                            data-testid="policy-save"
                            disabled={saveMutation.isPending || !dirty}
                            onClick={() => saveMutation.mutate()}
                        >
                            Save
                        </Button>
                    </div>
                </div>

                <PolicyYamlEditor
                    value={draft}
                    onChange={(next) => {
                        setDraft(next);
                        if (errorText) {
                            clearError();
                        }
                        setStatusMessage(null);
                        setValidateDiff(null);
                    }}
                    diagnostics={diagnostics}
                />

                {errorText ? (
                    <div className="space-y-2" data-testid="policy-lint">
                        <p
                            className="text-status-error-fg text-sm whitespace-pre-wrap"
                            role="alert"
                            data-testid="policy-error"
                        >
                            {errorText}
                        </p>
                        <ul className="text-status-error-fg space-y-1 text-xs">
                            {diagnostics.map((item) => (
                                <li
                                    key={`${item.line}-${item.message.slice(0, 24)}`}
                                    data-testid="policy-lint-line"
                                    data-line={String(item.line)}
                                >
                                    Line {item.line}
                                </li>
                            ))}
                        </ul>
                    </div>
                ) : null}

                {statusMessage ? (
                    <p
                        className="text-muted-foreground text-sm"
                        role="status"
                        data-testid="policy-status"
                    >
                        {statusMessage}
                    </p>
                ) : null}
            </section>

            {dirty ? (
                <section className="space-y-2" data-testid="policy-diff">
                    <h2 className="text-sm font-medium">Diff before save</h2>
                    <div className="grid gap-3 md:grid-cols-2">
                        <DiffPane
                            label="Stored"
                            text={storedYaml}
                            testId="policy-diff-stored"
                        />
                        <DiffPane
                            label="Draft"
                            text={draft}
                            testId="policy-diff-draft"
                        />
                    </div>
                    {validateDiff ? (
                        <pre
                            className="border-border bg-muted/40 max-h-48 overflow-auto rounded-md border p-3 font-mono text-xs"
                            data-testid="policy-diff-unified"
                        >
                            {validateDiff}
                        </pre>
                    ) : null}
                </section>
            ) : null}

            <section className="space-y-3" data-testid="policy-dry-run-panel">
                <h2 className="text-sm font-medium">Dry run</h2>
                <p className="text-muted-foreground text-xs">
                    Evaluates against the stored policy (save draft changes first).
                </p>
                <div className="flex flex-wrap items-end gap-3">
                    <label className="space-y-1 text-sm">
                        <span className="text-muted-foreground text-xs">Kind</span>
                        <select
                            className="border-border bg-background block h-9 rounded-md border px-2 text-sm"
                            value={dryKind}
                            data-testid="policy-dry-kind"
                            onChange={(event) =>
                                setDryKind(event.target.value as PolicyDryRunKind)
                            }
                        >
                            {DRY_KINDS.map((kind) => (
                                <option key={kind} value={kind}>
                                    {kind}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="min-w-[12rem] flex-1 space-y-1 text-sm">
                        <span className="text-muted-foreground text-xs">Resource</span>
                        <input
                            className="border-border bg-background block h-9 w-full rounded-md border px-2 font-mono text-sm"
                            value={dryResource}
                            data-testid="policy-dry-resource"
                            onChange={(event) => setDryResource(event.target.value)}
                            required
                        />
                    </label>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="policy-dry-run"
                        disabled={
                            dryRunMutation.isPending || dryResource.trim().length === 0
                        }
                        onClick={() => dryRunMutation.mutate()}
                    >
                        Dry run
                    </Button>
                </div>
                {dryError ? (
                    <p
                        className="text-status-error-fg text-sm"
                        role="alert"
                        data-testid="policy-dry-error"
                    >
                        {dryError}
                    </p>
                ) : null}
                {dryResult ? (
                    <div
                        className="border-border flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm"
                        data-testid="policy-dry-result"
                    >
                        {isEffect(dryResult.effect) ? (
                            <EffectBadge effect={dryResult.effect} />
                        ) : (
                            <span data-testid="policy-dry-effect">
                                {dryResult.effect}
                            </span>
                        )}
                        <span className="text-muted-foreground">
                            {dryResult.reason}
                        </span>
                        {dryResult.rule ? (
                            <span
                                className="font-mono text-xs"
                                data-testid="policy-dry-rule"
                            >
                                {dryResult.rule}
                            </span>
                        ) : null}
                    </div>
                ) : null}
            </section>
        </div>
    );
}

function DiffPane({
    label,
    text,
    testId,
}: {
    label: string;
    text: string;
    testId: string;
}) {
    return (
        <div className="space-y-1">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                {label}
            </h3>
            <pre
                className="border-border bg-muted/40 max-h-64 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap"
                data-testid={testId}
            >
                {text || "—"}
            </pre>
        </div>
    );
}

function isEffect(value: string): value is EffectKind {
    return value === "allow" || value === "deny" || value === "require_approval";
}
