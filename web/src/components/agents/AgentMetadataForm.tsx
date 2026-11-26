import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState, type FormEvent } from "react";

import { patchAgent, type Agent, type Owner, type PatchAgentBody } from "@/api/agents";
import { TesseraApiError, type FieldErrors } from "@/api/client";
import { agentsKeys } from "@/api/keys";
import { SettingsField } from "@/components/settings/SettingsField";
import { Button } from "@/components/ui/button";
import { AGENT_AUTONOMY } from "@/lib/agents-search";
import { settingsInputClass } from "@/lib/settings-form";

const RISK_TIERS = ["low", "medium", "high"] as const;

export type AgentMetadataFormProps = {
    agent: Agent;
    owners: Owner[];
};

type FormState = {
    description: string;
    owner_id: string;
    risk_tier: (typeof RISK_TIERS)[number];
    autonomy: (typeof AGENT_AUTONOMY)[number];
    supervisors: string;
    labels: string;
};

function formFromAgent(agent: Agent): FormState {
    const risk = RISK_TIERS.includes(agent.risk_tier as (typeof RISK_TIERS)[number])
        ? (agent.risk_tier as (typeof RISK_TIERS)[number])
        : "low";
    const autonomy = AGENT_AUTONOMY.includes(
        agent.autonomy as (typeof AGENT_AUTONOMY)[number],
    )
        ? (agent.autonomy as (typeof AGENT_AUTONOMY)[number])
        : "interactive";
    return {
        description: agent.description ?? "",
        owner_id: agent.owner_id,
        risk_tier: risk,
        autonomy,
        supervisors: (agent.supervisors ?? []).join(", "),
        labels: Object.entries(agent.labels ?? {})
            .map(([key, value]) => `${key}=${value}`)
            .join("\n"),
    };
}

function parseLabels(raw: string): Record<string, string> | FieldErrors {
    const labels: Record<string, string> = {};
    for (const line of raw.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed) {
            continue;
        }
        const eq = trimmed.indexOf("=");
        if (eq <= 0) {
            return { labels: "Each label must be key=value on its own line." };
        }
        const key = trimmed.slice(0, eq).trim();
        const value = trimmed.slice(eq + 1).trim();
        if (!key) {
            return { labels: "Each label must be key=value on its own line." };
        }
        labels[key] = value;
    }
    return labels;
}

/**
 * Edit agent registry metadata via `PATCH /v1/agents/{id}`.
 */
export function AgentMetadataForm({ agent, owners }: AgentMetadataFormProps) {
    const queryClient = useQueryClient();
    const descriptionId = useId();
    const ownerId = useId();
    const riskId = useId();
    const autonomyId = useId();
    const supervisorsId = useId();
    const labelsId = useId();

    const [form, setForm] = useState<FormState>(() => formFromAgent(agent));
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [flash, setFlash] = useState<string | null>(null);

    useEffect(() => {
        setForm(formFromAgent(agent));
        setFieldErrors({});
        setFormError(null);
    }, [agent]);

    const mutation = useMutation({
        mutationFn: (body: PatchAgentBody) => patchAgent(agent.id, body),
        onSuccess: async () => {
            setFlash("Metadata saved");
            setFieldErrors({});
            setFormError(null);
            await queryClient.invalidateQueries({
                queryKey: agentsKeys.detail(agent.id),
            });
            await queryClient.invalidateQueries({ queryKey: agentsKeys.all });
        },
    });

    const activeOwners = owners.filter(
        (owner) => owner.active || owner.id === agent.owner_id,
    );

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFlash(null);
        setFormError(null);
        const labelsOrError = parseLabels(form.labels);
        if ("labels" in labelsOrError && typeof labelsOrError.labels === "string") {
            setFieldErrors(labelsOrError);
            return;
        }
        const supervisors = form.supervisors
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean);
        setFieldErrors({});
        mutation.mutate(
            {
                description: form.description,
                owner_id: form.owner_id,
                risk_tier: form.risk_tier,
                autonomy: form.autonomy,
                supervisors,
                labels: labelsOrError as Record<string, string>,
            },
            {
                onError: (err) => {
                    if (!(err instanceof TesseraApiError)) {
                        setFormError("Could not save metadata");
                        return;
                    }
                    if (err.status === 422 && Object.keys(err.fieldErrors).length > 0) {
                        setFieldErrors(err.fieldErrors);
                        return;
                    }
                    setFormError(err.message);
                },
            },
        );
    };

    return (
        <section
            className="border-border bg-panel space-y-4 rounded-md border p-4"
            data-testid="agent-metadata-form"
            aria-labelledby="agent-metadata-heading"
        >
            <h2
                id="agent-metadata-heading"
                className="text-foreground text-sm font-semibold"
            >
                Metadata
            </h2>
            {flash ? (
                <p className="text-muted-foreground text-sm" role="status">
                    {flash}
                </p>
            ) : null}
            <form className="grid max-w-xl gap-3" onSubmit={onSubmit}>
                <SettingsField
                    label="Description"
                    htmlFor={descriptionId}
                    error={fieldErrors.description}
                >
                    <textarea
                        id={descriptionId}
                        name="description"
                        rows={2}
                        value={form.description}
                        data-testid="agent-meta-description"
                        className={settingsInputClass(fieldErrors.description)}
                        onChange={(event) =>
                            setForm((prev) => ({
                                ...prev,
                                description: event.target.value,
                            }))
                        }
                    />
                </SettingsField>
                <SettingsField
                    label="Owner"
                    htmlFor={ownerId}
                    error={fieldErrors.owner_id}
                >
                    <select
                        id={ownerId}
                        name="owner_id"
                        value={form.owner_id}
                        data-testid="agent-meta-owner"
                        className={settingsInputClass(fieldErrors.owner_id)}
                        onChange={(event) =>
                            setForm((prev) => ({
                                ...prev,
                                owner_id: event.target.value,
                            }))
                        }
                    >
                        {activeOwners.map((owner) => (
                            <option key={owner.id} value={owner.id}>
                                {owner.display_name} ({owner.email})
                                {!owner.active ? " — inactive" : ""}
                            </option>
                        ))}
                    </select>
                </SettingsField>
                <div className="grid gap-3 sm:grid-cols-2">
                    <SettingsField
                        label="Risk tier"
                        htmlFor={riskId}
                        error={fieldErrors.risk_tier}
                    >
                        <select
                            id={riskId}
                            name="risk_tier"
                            value={form.risk_tier}
                            data-testid="agent-meta-risk"
                            className={settingsInputClass(fieldErrors.risk_tier)}
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    risk_tier: event.target
                                        .value as FormState["risk_tier"],
                                }))
                            }
                        >
                            {RISK_TIERS.map((tier) => (
                                <option key={tier} value={tier}>
                                    {tier}
                                </option>
                            ))}
                        </select>
                    </SettingsField>
                    <SettingsField
                        label="Autonomy"
                        htmlFor={autonomyId}
                        error={fieldErrors.autonomy}
                    >
                        <select
                            id={autonomyId}
                            name="autonomy"
                            value={form.autonomy}
                            data-testid="agent-meta-autonomy"
                            className={settingsInputClass(fieldErrors.autonomy)}
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    autonomy: event.target
                                        .value as FormState["autonomy"],
                                }))
                            }
                        >
                            {AGENT_AUTONOMY.map((level) => (
                                <option key={level} value={level}>
                                    {level}
                                </option>
                            ))}
                        </select>
                    </SettingsField>
                </div>
                <SettingsField
                    label="Supervisors"
                    htmlFor={supervisorsId}
                    error={fieldErrors.supervisors}
                >
                    <input
                        id={supervisorsId}
                        name="supervisors"
                        value={form.supervisors}
                        placeholder="owner ids, comma-separated"
                        data-testid="agent-meta-supervisors"
                        className={settingsInputClass(fieldErrors.supervisors)}
                        onChange={(event) =>
                            setForm((prev) => ({
                                ...prev,
                                supervisors: event.target.value,
                            }))
                        }
                    />
                </SettingsField>
                <SettingsField
                    label="Labels"
                    htmlFor={labelsId}
                    error={fieldErrors.labels}
                >
                    <textarea
                        id={labelsId}
                        name="labels"
                        rows={3}
                        value={form.labels}
                        placeholder={"team=payments\nenv=prod"}
                        data-testid="agent-meta-labels"
                        className={settingsInputClass(fieldErrors.labels)}
                        onChange={(event) =>
                            setForm((prev) => ({
                                ...prev,
                                labels: event.target.value,
                            }))
                        }
                    />
                </SettingsField>
                {formError ? (
                    <p
                        className="text-status-error-fg text-sm"
                        role="alert"
                        data-testid="agent-meta-error"
                    >
                        {formError}
                    </p>
                ) : null}
                <div>
                    <Button
                        type="submit"
                        size="sm"
                        disabled={mutation.isPending}
                        data-testid="agent-meta-save"
                    >
                        {mutation.isPending ? "Saving…" : "Save metadata"}
                    </Button>
                </div>
            </form>
        </section>
    );
}
