import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    useEffect,
    useId,
    useRef,
    useState,
    type FormEvent,
    type ReactNode,
} from "react";

import { createAgent, fetchOwners, type CreateAgentBody } from "@/api/agents";
import { TesseraApiError, type FieldErrors } from "@/api/client";
import { agentsKeys, ownersKeys } from "@/api/keys";
import { Button } from "@/components/ui/button";
import { AGENT_AUTONOMY } from "@/lib/agents-search";
import { ENVIRONMENT_OPTIONS } from "@/lib/environment";
import { cn } from "@/lib/utils";

const RISK_TIERS = ["low", "medium", "high"] as const;

const NAME_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;

export type CreateAgentDialogProps = {
    open: boolean;
    defaultEnvironment?: string;
    onClose: () => void;
    onCreated: (agentId: string) => void;
};

type FormState = {
    name: string;
    environment: string;
    owner_id: string;
    description: string;
    risk_tier: (typeof RISK_TIERS)[number];
    autonomy: (typeof AGENT_AUTONOMY)[number];
    supervisors: string;
};

function emptyForm(environment: string): FormState {
    return {
        name: "",
        environment: environment || "dev",
        owner_id: "",
        description: "",
        risk_tier: "low",
        autonomy: "interactive",
        supervisors: "",
    };
}

/**
 * Modal to register a draft agent via `POST /v1/agents`.
 *
 * Maps 422 `fieldErrors` onto inputs and places 409 conflict messages on name.
 */
export function CreateAgentDialog({
    open,
    defaultEnvironment = "dev",
    onClose,
    onCreated,
}: CreateAgentDialogProps) {
    const titleId = useId();
    const dialogRef = useRef<HTMLDivElement>(null);
    const queryClient = useQueryClient();
    const [form, setForm] = useState<FormState>(() => emptyForm(defaultEnvironment));
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);

    const ownersQuery = useQuery({
        queryKey: ownersKeys.list({ limit: 200 }),
        queryFn: () => fetchOwners({ limit: 200 }),
        enabled: open,
    });

    const createMutation = useMutation({
        mutationFn: createAgent,
        onSuccess: async (agent) => {
            await queryClient.invalidateQueries({ queryKey: agentsKeys.all });
            onCreated(agent.id);
        },
    });

    useEffect(() => {
        if (!open) {
            return;
        }
        setForm(emptyForm(defaultEnvironment));
        setFieldErrors({});
        setFormError(null);
        const root = dialogRef.current;
        const focusable = root?.querySelector<HTMLElement>(
            "input,select,textarea,button,[href],[tabindex]:not([tabindex='-1'])",
        );
        focusable?.focus();
    }, [open, defaultEnvironment]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose]);

    useEffect(() => {
        if (!open || form.owner_id || !ownersQuery.data?.items.length) {
            return;
        }
        const first = ownersQuery.data.items.find((owner) => owner.active);
        if (first) {
            setForm((prev) => ({ ...prev, owner_id: first.id }));
        }
    }, [open, form.owner_id, ownersQuery.data]);

    if (!open) {
        return null;
    }

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
        if (!form.owner_id) {
            local.owner_id = "Owner is required";
        }
        if (Object.keys(local).length > 0) {
            setFieldErrors(local);
            return;
        }

        const supervisors = form.supervisors
            .split(/[\n,]+/)
            .map((part) => part.trim())
            .filter(Boolean);

        const body: CreateAgentBody = {
            name,
            environment,
            owner_id: form.owner_id,
            description: form.description.trim(),
            risk_tier: form.risk_tier,
            autonomy: form.autonomy,
            supervisors,
        };

        createMutation.mutate(body, {
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

    const owners = (ownersQuery.data?.items ?? []).filter((owner) => owner.active);
    const busy = createMutation.isPending;

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            data-testid="create-agent-dialog-root"
            role="presentation"
        >
            <button
                type="button"
                className="bg-foreground/30 absolute inset-0 cursor-default border-0"
                aria-label="Close create agent backdrop"
                data-testid="create-agent-backdrop"
                onClick={onClose}
            />
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                data-testid="create-agent-dialog"
                className="border-border bg-panel relative z-10 max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-md border p-4 shadow-sm"
            >
                <form className="space-y-4" onSubmit={submit} noValidate>
                    <div className="space-y-1">
                        <h2 id={titleId} className="text-sm font-semibold">
                            Create agent
                        </h2>
                        <p className="text-muted-foreground text-sm">
                            Registers a draft agent. Upload a policy and activate it
                            from the detail page.
                        </p>
                    </div>

                    <Field
                        label="Name"
                        htmlFor="create-agent-name"
                        error={fieldErrors.name}
                    >
                        <input
                            id="create-agent-name"
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
                        htmlFor="create-agent-environment"
                        error={fieldErrors.environment}
                    >
                        <input
                            id="create-agent-environment"
                            name="environment"
                            list="create-agent-environments"
                            value={form.environment}
                            data-testid="create-agent-environment"
                            aria-invalid={Boolean(fieldErrors.environment)}
                            className={inputClass(fieldErrors.environment)}
                            onChange={(event) =>
                                setField("environment", event.target.value)
                            }
                            autoComplete="off"
                            spellCheck={false}
                        />
                        <datalist id="create-agent-environments">
                            {ENVIRONMENT_OPTIONS.map((env) => (
                                <option key={env} value={env} />
                            ))}
                        </datalist>
                    </Field>

                    <Field
                        label="Owner"
                        htmlFor="create-agent-owner"
                        error={fieldErrors.owner_id}
                    >
                        <select
                            id="create-agent-owner"
                            name="owner_id"
                            value={form.owner_id}
                            data-testid="create-agent-owner"
                            aria-invalid={Boolean(fieldErrors.owner_id)}
                            className={inputClass(fieldErrors.owner_id)}
                            onChange={(event) =>
                                setField("owner_id", event.target.value)
                            }
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

                    <Field
                        label="Description"
                        htmlFor="create-agent-description"
                        error={fieldErrors.description}
                    >
                        <textarea
                            id="create-agent-description"
                            name="description"
                            rows={2}
                            value={form.description}
                            data-testid="create-agent-description"
                            className={inputClass(fieldErrors.description)}
                            onChange={(event) =>
                                setField("description", event.target.value)
                            }
                        />
                    </Field>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <Field
                            label="Risk tier"
                            htmlFor="create-agent-risk"
                            error={fieldErrors.risk_tier}
                        >
                            <select
                                id="create-agent-risk"
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
                            htmlFor="create-agent-autonomy"
                            error={fieldErrors.autonomy}
                        >
                            <select
                                id="create-agent-autonomy"
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
                        htmlFor="create-agent-supervisors"
                        error={fieldErrors.supervisors}
                        hint="Optional. Comma or newline separated (user:… / group:…)."
                    >
                        <textarea
                            id="create-agent-supervisors"
                            name="supervisors"
                            rows={2}
                            value={form.supervisors}
                            data-testid="create-agent-supervisors"
                            className={inputClass(fieldErrors.supervisors)}
                            onChange={(event) =>
                                setField("supervisors", event.target.value)
                            }
                        />
                    </Field>

                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="create-agent-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}

                    <div className="flex justify-end gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            data-testid="create-agent-cancel"
                            onClick={onClose}
                            disabled={busy}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="submit"
                            size="sm"
                            data-testid="create-agent-submit"
                            disabled={busy}
                        >
                            {busy ? "Creating…" : "Create agent"}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
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
