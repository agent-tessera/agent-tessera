import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { TesseraApiError, type FieldErrors } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import {
    ALERT_METRICS,
    createAlert,
    deleteAlert,
    fetchAlerts,
    type AlertAction,
    type AlertMetric,
} from "@/api/settings";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SettingsField } from "@/components/settings/SettingsField";
import { settingsInputClass } from "@/lib/settings-form";
import { Button } from "@/components/ui/button";
import { ENVIRONMENT_OPTIONS } from "@/lib/environment";

type FormState = {
    name: string;
    metric: AlertMetric;
    threshold: string;
    window: string;
    environment: string;
    action: "notify" | "suspend";
};

const EMPTY_FORM: FormState = {
    name: "",
    metric: "denials",
    threshold: "1",
    window: "1h",
    environment: "",
    action: "notify",
};

/**
 * Alerts settings: list, create (with 422 field mapping), delete confirm.
 */
export function AlertsSettings() {
    const queryClient = useQueryClient();
    const nameId = useId();
    const metricId = useId();
    const thresholdId = useId();
    const windowId = useId();
    const environmentId = useId();
    const actionId = useId();

    const [form, setForm] = useState<FormState>(EMPTY_FORM);
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [pendingDelete, setPendingDelete] = useState<{
        id: string;
        name: string;
    } | null>(null);

    const listQuery = useQuery({
        queryKey: settingsKeys.alerts(),
        queryFn: fetchAlerts,
    });

    const createMutation = useMutation({
        mutationFn: createAlert,
        onSuccess: async () => {
            setForm(EMPTY_FORM);
            setFieldErrors({});
            setFormError(null);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.alerts(),
            });
        },
    });

    const deleteMutation = useMutation({
        mutationFn: deleteAlert,
        onSuccess: async () => {
            setPendingDelete(null);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.alerts(),
            });
        },
    });

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        const threshold = Number(form.threshold);
        if (!Number.isFinite(threshold) || threshold <= 0) {
            setFieldErrors({ threshold: "threshold must be positive" });
            return;
        }
        const labels: Record<string, string> = {};
        if (form.environment.trim()) {
            labels.environment = form.environment.trim();
        }
        const action: AlertAction = form.action === "notify" ? "webhook" : "suspend";
        createMutation.mutate(
            {
                name: form.name.trim(),
                metric: form.metric,
                threshold,
                window: form.window.trim(),
                selector: { labels },
                action,
            },
            {
                onError: (err) => {
                    if (!(err instanceof TesseraApiError)) {
                        setFormError("Could not create alert");
                        return;
                    }
                    if (err.status === 422 && Object.keys(err.fieldErrors).length > 0) {
                        setFieldErrors(err.fieldErrors);
                        return;
                    }
                    if (err.status === 409) {
                        setFieldErrors({ name: err.message });
                        return;
                    }
                    setFormError(err.message);
                },
            },
        );
    };

    if (listQuery.isError) {
        return (
            <ErrorState
                title="Could not load alerts"
                message={
                    listQuery.error instanceof Error
                        ? listQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void listQuery.refetch()}
            />
        );
    }

    const items = listQuery.data?.items ?? [];

    return (
        <div className="space-y-8" data-testid="settings-alerts">
            {items.length === 0 && !listQuery.isLoading ? (
                <EmptyState
                    title="No alert rules"
                    body="Create a rule below. The same validation as POST /v1/alerts applies."
                />
            ) : (
                <div className="border-border overflow-x-auto rounded-md border">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-muted/40 text-muted-foreground text-xs tracking-wide uppercase">
                            <tr>
                                <th className="px-3 py-2 font-medium">Name</th>
                                <th className="px-3 py-2 font-medium">Metric</th>
                                <th className="px-3 py-2 font-medium">Threshold</th>
                                <th className="px-3 py-2 font-medium">Window</th>
                                <th className="px-3 py-2 font-medium">Environment</th>
                                <th className="px-3 py-2 font-medium">Action</th>
                                <th className="px-3 py-2 font-medium" />
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((alert) => (
                                <tr
                                    key={alert.id}
                                    className="border-border border-t"
                                    data-testid={`alert-row-${alert.id}`}
                                >
                                    <td className="px-3 py-2">{alert.name}</td>
                                    <td className="px-3 py-2 font-mono text-[13px]">
                                        {alert.metric}
                                    </td>
                                    <td className="px-3 py-2">{alert.threshold}</td>
                                    <td className="px-3 py-2">{alert.window}</td>
                                    <td className="px-3 py-2">
                                        {alert.selector?.labels?.environment ?? "—"}
                                    </td>
                                    <td className="px-3 py-2">
                                        {alert.action === "webhook"
                                            ? "notify"
                                            : alert.action}
                                    </td>
                                    <td className="px-3 py-2 text-right">
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                            data-testid={`alert-delete-${alert.id}`}
                                            onClick={() =>
                                                setPendingDelete({
                                                    id: alert.id,
                                                    name: alert.name,
                                                })
                                            }
                                        >
                                            Delete
                                        </Button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            <section className="space-y-4" aria-labelledby="create-alert-heading">
                <h2
                    id="create-alert-heading"
                    className="text-foreground text-base font-semibold"
                >
                    Create alert
                </h2>
                <form
                    className="grid max-w-xl gap-3"
                    onSubmit={onSubmit}
                    data-testid="alert-create-form"
                >
                    <SettingsField
                        label="Name"
                        htmlFor={nameId}
                        error={fieldErrors.name}
                    >
                        <input
                            id={nameId}
                            name="name"
                            required
                            value={form.name}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    name: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.name)}
                            className={settingsInputClass(fieldErrors.name)}
                            data-testid="alert-name"
                        />
                    </SettingsField>
                    <SettingsField
                        label="Metric"
                        htmlFor={metricId}
                        error={fieldErrors.metric}
                    >
                        <select
                            id={metricId}
                            name="metric"
                            value={form.metric}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    metric: e.target.value as AlertMetric,
                                }))
                            }
                            className={settingsInputClass(fieldErrors.metric)}
                            data-testid="alert-metric"
                        >
                            {ALERT_METRICS.map((metric) => (
                                <option key={metric} value={metric}>
                                    {metric}
                                </option>
                            ))}
                        </select>
                    </SettingsField>
                    <SettingsField
                        label="Threshold"
                        htmlFor={thresholdId}
                        error={fieldErrors.threshold}
                    >
                        <input
                            id={thresholdId}
                            name="threshold"
                            type="number"
                            step="any"
                            min="0"
                            required
                            value={form.threshold}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    threshold: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.threshold)}
                            className={settingsInputClass(fieldErrors.threshold)}
                            data-testid="alert-threshold"
                        />
                    </SettingsField>
                    <SettingsField
                        label="Window"
                        htmlFor={windowId}
                        error={fieldErrors.window}
                        hint="Duration such as 30s, 10m, or 1h"
                    >
                        <input
                            id={windowId}
                            name="window"
                            required
                            value={form.window}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    window: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.window)}
                            className={settingsInputClass(fieldErrors.window)}
                            data-testid="alert-window"
                        />
                    </SettingsField>
                    <SettingsField
                        label="Environment"
                        htmlFor={environmentId}
                        error={
                            fieldErrors["selector.labels.environment"] ??
                            fieldErrors.environment
                        }
                    >
                        <select
                            id={environmentId}
                            name="environment"
                            value={form.environment}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    environment: e.target.value,
                                }))
                            }
                            className={settingsInputClass()}
                            data-testid="alert-environment"
                        >
                            <option value="">Any</option>
                            {ENVIRONMENT_OPTIONS.map((env) => (
                                <option key={env} value={env}>
                                    {env}
                                </option>
                            ))}
                        </select>
                    </SettingsField>
                    <SettingsField
                        label="Action"
                        htmlFor={actionId}
                        error={fieldErrors.action}
                    >
                        <select
                            id={actionId}
                            name="action"
                            value={form.action}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    action: e.target.value as "notify" | "suspend",
                                }))
                            }
                            className={settingsInputClass(fieldErrors.action)}
                            data-testid="alert-action"
                        >
                            <option value="notify">Notify</option>
                            <option value="suspend">Suspend</option>
                        </select>
                    </SettingsField>
                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="alert-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}
                    <div>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={createMutation.isPending}
                            data-testid="alert-create-submit"
                        >
                            {createMutation.isPending ? "Creating…" : "Create alert"}
                        </Button>
                    </div>
                </form>
            </section>

            <ConfirmDialog
                open={pendingDelete !== null}
                title="Delete alert"
                sentence={pendingDelete ? `Delete alert ${pendingDelete.name}?` : ""}
                confirmLabel="Delete"
                destructive
                onCancel={() => setPendingDelete(null)}
                onConfirm={() => {
                    if (pendingDelete) {
                        deleteMutation.mutate(pendingDelete.id);
                    }
                }}
            />
        </div>
    );
}
