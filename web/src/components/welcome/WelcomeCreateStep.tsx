import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import {
    createAgent,
    createOwner,
    fetchOwners,
    type CreateAgentBody,
} from "@/api/agents";
import { TesseraApiError, type FieldErrors } from "@/api/client";
import { agentsKeys, ownersKeys } from "@/api/keys";
import { Button } from "@/components/ui/button";
import { AGENT_AUTONOMY } from "@/lib/agents-search";
import { ENVIRONMENT_OPTIONS } from "@/lib/environment";
import { cn } from "@/lib/utils";

const RISK_TIERS = ["low", "medium", "high"] as const;
const NAME_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;

type FormState = {
    name: string;
    environment: string;
    owner_id: string;
    owner_email: string;
    owner_display_name: string;
    description: string;
    risk_tier: (typeof RISK_TIERS)[number];
    autonomy: (typeof AGENT_AUTONOMY)[number];
    supervisors: string;
};

export type WelcomeCreateStepProps = {
    onCreated: (agentId: string, agentName: string, environment: string) => void;
};

/**
 * Step 1: create the first agent (same fields as the Agents create dialog).
 *
 * When no owners exist yet, collects email + display name and creates one.
 */
export function WelcomeCreateStep({ onCreated }: WelcomeCreateStepProps) {
    const queryClient = useQueryClient();
    const [form, setForm] = useState<FormState>({
        name: "",
        environment: "dev",
        owner_id: "",
        owner_email: "",
        owner_display_name: "",
        description: "",
        risk_tier: "low",
        autonomy: "interactive",
        supervisors: "",
    });
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);

    const ownersQuery = useQuery({
        queryKey: ownersKeys.list({ limit: 200 }),
        queryFn: () => fetchOwners({ limit: 200 }),
    });

    const owners = useMemo(
        () => ownersQuery.data?.items ?? [],
        [ownersQuery.data?.items],
    );
    const needsOwner = !ownersQuery.isLoading && owners.length === 0;

    useEffect(() => {
        if (form.owner_id || !owners.length) {
            return;
        }
        const first = owners.find((owner) => owner.active) ?? owners[0];
        if (first) {
            setForm((prev) => ({ ...prev, owner_id: first.id }));
        }
    }, [form.owner_id, owners]);

    const createMutation = useMutation({
        mutationFn: async () => {
            let ownerId = form.owner_id;
            if (needsOwner) {
                const email = form.owner_email.trim();
                const display =
                    form.owner_display_name.trim() || email.split("@")[0] || "owner";
                const owner = await createOwner({
                    type: "user",
                    email,
                    display_name: display,
                });
                ownerId = owner.id;
                await queryClient.invalidateQueries({ queryKey: ownersKeys.all });
            }
            const supervisors = form.supervisors
                .split(/[\n,]+/)
                .map((part) => part.trim())
                .filter(Boolean);
            const body: CreateAgentBody = {
                name: form.name.trim(),
                environment: form.environment.trim() || "dev",
                owner_id: ownerId,
                description: form.description.trim(),
                risk_tier: form.risk_tier,
                autonomy: form.autonomy,
                supervisors,
            };
            return createAgent(body);
        },
        onSuccess: async (agent) => {
            await queryClient.invalidateQueries({ queryKey: agentsKeys.all });
            onCreated(agent.id, agent.name, agent.environment);
        },
    });

    const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
        setForm((prev) => ({ ...prev, [key]: value }));
        setFieldErrors((prev) => {
            if (!prev[key]) {
                return prev;
            }
            const next = { ...prev };
            delete next[key];
            return next;
        });
    };

    const submit = (event: FormEvent) => {
        event.preventDefault();
        setFormError(null);
        const local: FieldErrors = {};
        const name = form.name.trim();
        const environment = form.environment.trim();
        if (!name) {
            local.name = "Name is required";
        } else if (!NAME_PATTERN.test(name) || name.length > 63) {
            local.name =
                "Use lowercase letters, digits, and single hyphens or underscores (max 63)";
        }
        if (!environment) {
            local.environment = "Environment is required";
        } else if (!NAME_PATTERN.test(environment) || environment.length > 63) {
            local.environment =
                "Use lowercase letters, digits, and single hyphens or underscores (max 63)";
        }
        if (needsOwner) {
            if (!form.owner_email.trim()) {
                local.owner_email = "Owner email is required";
            }
        } else if (!form.owner_id) {
            local.owner_id = "Owner is required";
        }
        if (Object.keys(local).length > 0) {
            setFieldErrors(local);
            return;
        }
        createMutation.mutate(undefined, {
            onError: (err) => {
                if (!(err instanceof TesseraApiError)) {
                    setFormError("Could not create agent");
                    return;
                }
                if (err.status === 409) {
                    setFieldErrors({ name: err.message });
                    return;
                }
                if (err.status === 422 && Object.keys(err.fieldErrors).length > 0) {
                    setFieldErrors(err.fieldErrors);
                    return;
                }
                setFormError(err.message);
            },
        });
    };

    const busy = createMutation.isPending;

    return (
        <form
            className="space-y-4"
            onSubmit={submit}
            noValidate
            data-testid="welcome-create-form"
        >
            <div className="space-y-1">
                <h2 className="text-sm font-semibold">Create your first agent</h2>
                <p className="text-muted-foreground text-sm">
                    Registers a draft agent. Next you will attach a policy template and
                    connect a runtime.
                </p>
            </div>

            <Field label="Name" htmlFor="welcome-agent-name" error={fieldErrors.name}>
                <input
                    id="welcome-agent-name"
                    name="name"
                    value={form.name}
                    data-testid="create-agent-name"
                    aria-invalid={Boolean(fieldErrors.name)}
                    className={inputClass(fieldErrors.name)}
                    onChange={(event) => setField("name", event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                />
            </Field>

            <Field
                label="Environment"
                htmlFor="welcome-agent-environment"
                error={fieldErrors.environment}
            >
                <input
                    id="welcome-agent-environment"
                    name="environment"
                    list="welcome-agent-environments"
                    value={form.environment}
                    data-testid="create-agent-environment"
                    aria-invalid={Boolean(fieldErrors.environment)}
                    className={inputClass(fieldErrors.environment)}
                    onChange={(event) => setField("environment", event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                />
                <datalist id="welcome-agent-environments">
                    {ENVIRONMENT_OPTIONS.map((env) => (
                        <option key={env} value={env} />
                    ))}
                </datalist>
            </Field>

            {needsOwner ? (
                <>
                    <Field
                        label="Owner email"
                        htmlFor="welcome-owner-email"
                        error={fieldErrors.owner_email}
                    >
                        <input
                            id="welcome-owner-email"
                            name="owner_email"
                            type="email"
                            value={form.owner_email}
                            data-testid="welcome-owner-email"
                            aria-invalid={Boolean(fieldErrors.owner_email)}
                            className={inputClass(fieldErrors.owner_email)}
                            onChange={(event) =>
                                setField("owner_email", event.target.value)
                            }
                            autoComplete="off"
                        />
                    </Field>
                    <Field
                        label="Owner display name"
                        htmlFor="welcome-owner-display"
                        error={fieldErrors.owner_display_name}
                        hint="Optional. Defaults to the email local part."
                    >
                        <input
                            id="welcome-owner-display"
                            name="owner_display_name"
                            value={form.owner_display_name}
                            data-testid="welcome-owner-display"
                            className={inputClass(fieldErrors.owner_display_name)}
                            onChange={(event) =>
                                setField("owner_display_name", event.target.value)
                            }
                            autoComplete="off"
                        />
                    </Field>
                </>
            ) : (
                <Field
                    label="Owner"
                    htmlFor="welcome-agent-owner"
                    error={fieldErrors.owner_id}
                >
                    <select
                        id="welcome-agent-owner"
                        name="owner_id"
                        value={form.owner_id}
                        data-testid="create-agent-owner"
                        aria-invalid={Boolean(fieldErrors.owner_id)}
                        className={inputClass(fieldErrors.owner_id)}
                        onChange={(event) => setField("owner_id", event.target.value)}
                        disabled={ownersQuery.isLoading}
                    >
                        <option value="">
                            {ownersQuery.isLoading
                                ? "Loading owners…"
                                : "Select an owner"}
                        </option>
                        {owners.map((owner) => (
                            <option key={owner.id} value={owner.id}>
                                {owner.display_name} ({owner.email})
                            </option>
                        ))}
                    </select>
                </Field>
            )}

            <Field
                label="Description"
                htmlFor="welcome-agent-description"
                error={fieldErrors.description}
            >
                <textarea
                    id="welcome-agent-description"
                    name="description"
                    rows={2}
                    value={form.description}
                    data-testid="create-agent-description"
                    className={inputClass(fieldErrors.description)}
                    onChange={(event) => setField("description", event.target.value)}
                />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
                <Field
                    label="Risk tier"
                    htmlFor="welcome-agent-risk"
                    error={fieldErrors.risk_tier}
                >
                    <select
                        id="welcome-agent-risk"
                        name="risk_tier"
                        value={form.risk_tier}
                        data-testid="create-agent-risk"
                        className={inputClass(fieldErrors.risk_tier)}
                        onChange={(event) =>
                            setField(
                                "risk_tier",
                                event.target.value as FormState["risk_tier"],
                            )
                        }
                    >
                        {RISK_TIERS.map((tier) => (
                            <option key={tier} value={tier}>
                                {tier}
                            </option>
                        ))}
                    </select>
                </Field>

                <Field
                    label="Autonomy"
                    htmlFor="welcome-agent-autonomy"
                    error={fieldErrors.autonomy}
                >
                    <select
                        id="welcome-agent-autonomy"
                        name="autonomy"
                        value={form.autonomy}
                        data-testid="create-agent-autonomy"
                        className={inputClass(fieldErrors.autonomy)}
                        onChange={(event) =>
                            setField(
                                "autonomy",
                                event.target.value as FormState["autonomy"],
                            )
                        }
                    >
                        {AGENT_AUTONOMY.map((level) => (
                            <option key={level} value={level}>
                                {level}
                            </option>
                        ))}
                    </select>
                </Field>
            </div>

            <Field
                label="Supervisors"
                htmlFor="welcome-agent-supervisors"
                error={fieldErrors.supervisors}
                hint="Optional. Comma or newline separated (user:… / group:…)."
            >
                <textarea
                    id="welcome-agent-supervisors"
                    name="supervisors"
                    rows={2}
                    value={form.supervisors}
                    data-testid="create-agent-supervisors"
                    className={inputClass(fieldErrors.supervisors)}
                    onChange={(event) => setField("supervisors", event.target.value)}
                />
            </Field>

            {formError ? (
                <p
                    className="text-status-error text-sm"
                    role="alert"
                    data-testid="welcome-create-error"
                >
                    {formError}
                </p>
            ) : null}

            <Button
                type="submit"
                size="sm"
                data-testid="welcome-create-submit"
                disabled={busy}
            >
                {busy ? "Creating…" : "Create agent"}
            </Button>
        </form>
    );
}

function Field({
    label,
    htmlFor,
    error,
    hint,
    children,
}: {
    label: string;
    htmlFor: string;
    error?: string;
    hint?: string;
    children: ReactNode;
}) {
    return (
        <div className="space-y-1.5">
            <label
                htmlFor={htmlFor}
                className="text-muted-foreground text-xs tracking-wide uppercase"
            >
                {label}
            </label>
            {children}
            {hint && !error ? (
                <p className="text-muted-foreground text-xs">{hint}</p>
            ) : null}
            {error ? (
                <p
                    className="text-status-error text-xs"
                    role="alert"
                    data-testid={`field-error-${htmlFor}`}
                >
                    {error}
                </p>
            ) : null}
        </div>
    );
}

function inputClass(error?: string): string {
    return cn(
        "border-border bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none",
        error && "border-status-error",
    );
}
