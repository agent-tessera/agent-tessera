import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { TesseraApiError, type FieldErrors } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import {
    WEBHOOK_EVENT_OPTIONS,
    createWebhook,
    deleteWebhook,
    fetchWebhooks,
    pingWebhook,
    updateWebhook,
    type WebhookEndpoint,
} from "@/api/settings";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SettingsField } from "@/components/settings/SettingsField";
import { Button } from "@/components/ui/button";
import { settingsInputClass } from "@/lib/settings-form";
import { cn } from "@/lib/utils";

type FormState = {
    url: string;
    events: string[];
    secret: string;
    active: boolean;
    clear_secret: boolean;
};

const EMPTY_FORM: FormState = {
    url: "",
    events: [],
    secret: "",
    active: true,
    clear_secret: false,
};

function formFromWebhook(hook: WebhookEndpoint): FormState {
    return {
        url: hook.url,
        events: [...hook.events],
        secret: "",
        active: hook.active,
        clear_secret: false,
    };
}

/**
 * Webhooks settings: list, create, edit, Send test (ping), delete confirm.
 */
export function WebhooksSettings() {
    const queryClient = useQueryClient();
    const urlId = useId();
    const secretId = useId();

    const [form, setForm] = useState<FormState>(EMPTY_FORM);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [flash, setFlash] = useState<string | null>(null);
    const [pendingDelete, setPendingDelete] = useState<{
        id: string;
        url: string;
    } | null>(null);

    const listQuery = useQuery({
        queryKey: settingsKeys.webhooks(),
        queryFn: fetchWebhooks,
    });

    const createMutation = useMutation({
        mutationFn: createWebhook,
        onSuccess: async () => {
            setForm(EMPTY_FORM);
            setFieldErrors({});
            setFormError(null);
            setFlash("Webhook created");
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.webhooks(),
            });
        },
    });

    const updateMutation = useMutation({
        mutationFn: (input: {
            id: string;
            body: Parameters<typeof updateWebhook>[1];
        }) => updateWebhook(input.id, input.body),
        onSuccess: async () => {
            setForm(EMPTY_FORM);
            setEditingId(null);
            setFieldErrors({});
            setFormError(null);
            setFlash("Webhook updated");
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.webhooks(),
            });
        },
    });

    const pingMutation = useMutation({
        mutationFn: pingWebhook,
        onSuccess: () => {
            setFlash("Test event sent");
        },
        onError: (err) => {
            setFlash(null);
            setFormError(
                err instanceof TesseraApiError ? err.message : "Send test failed",
            );
        },
    });

    const deleteMutation = useMutation({
        mutationFn: deleteWebhook,
        onSuccess: async () => {
            setPendingDelete(null);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.webhooks(),
            });
        },
    });

    const toggleEvent = (event: string) => {
        setForm((prev) => {
            const has = prev.events.includes(event);
            return {
                ...prev,
                events: has
                    ? prev.events.filter((item) => item !== event)
                    : [...prev.events, event],
            };
        });
    };

    const startEdit = (hook: WebhookEndpoint) => {
        setEditingId(hook.id);
        setForm(formFromWebhook(hook));
        setFieldErrors({});
        setFormError(null);
        setFlash(null);
    };

    const cancelEdit = () => {
        setEditingId(null);
        setForm(EMPTY_FORM);
        setFieldErrors({});
        setFormError(null);
    };

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        setFlash(null);
        const url = form.url.trim();
        if (editingId) {
            updateMutation.mutate(
                {
                    id: editingId,
                    body: {
                        url,
                        events: form.events,
                        active: form.active,
                        ...(form.clear_secret
                            ? { clear_secret: true }
                            : form.secret.trim()
                              ? { secret: form.secret }
                              : {}),
                    },
                },
                {
                    onError: (err) => {
                        if (!(err instanceof TesseraApiError)) {
                            setFormError("Could not update webhook");
                            return;
                        }
                        if (
                            err.status === 422 &&
                            Object.keys(err.fieldErrors).length > 0
                        ) {
                            setFieldErrors(err.fieldErrors);
                            return;
                        }
                        setFormError(err.message);
                    },
                },
            );
            return;
        }
        createMutation.mutate(
            {
                url,
                events: form.events,
                secret: form.secret,
                active: form.active,
            },
            {
                onError: (err) => {
                    if (!(err instanceof TesseraApiError)) {
                        setFormError("Could not create webhook");
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

    if (listQuery.isError) {
        return (
            <ErrorState
                title="Could not load webhooks"
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
    const saving = createMutation.isPending || updateMutation.isPending;

    return (
        <div className="space-y-8" data-testid="settings-webhooks">
            {flash ? (
                <p
                    className="text-status-ok-fg text-sm"
                    role="status"
                    data-testid="webhook-flash"
                >
                    {flash}
                </p>
            ) : null}

            {items.length === 0 && !listQuery.isLoading ? (
                <EmptyState
                    title="No webhooks yet"
                    body="Add an endpoint below. Secrets are write-only and never shown again."
                    className="border-border bg-panel rounded-md border py-8"
                />
            ) : (
                <div className="border-border bg-panel overflow-x-auto rounded-md border">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-muted/40 text-muted-foreground text-xs tracking-wide uppercase">
                            <tr>
                                <th className="px-3 py-2 font-medium">URL</th>
                                <th className="px-3 py-2 font-medium">Events</th>
                                <th className="px-3 py-2 font-medium">Status</th>
                                <th className="px-3 py-2 font-medium">Secret</th>
                                <th className="px-3 py-2 font-medium" />
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((hook) => (
                                <tr
                                    key={hook.id}
                                    className="border-border border-t"
                                    data-testid={`webhook-row-${hook.id}`}
                                >
                                    <td className="px-3 py-2 font-mono text-[13px] break-all">
                                        {hook.url}
                                    </td>
                                    <td className="text-muted-foreground px-3 py-2 text-xs">
                                        {hook.events.length
                                            ? hook.events.join(", ")
                                            : "*"}
                                    </td>
                                    <td className="px-3 py-2">
                                        <span
                                            className={cn(
                                                "inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
                                                hook.active
                                                    ? "bg-status-ok-bg text-status-ok-fg border-status-ok/40"
                                                    : "bg-muted text-muted-foreground border-border",
                                            )}
                                        >
                                            {hook.active ? "Active" : "Inactive"}
                                        </span>
                                    </td>
                                    <td className="text-muted-foreground px-3 py-2 text-xs">
                                        {hook.has_secret ? "set" : "server default"}
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex flex-wrap justify-end gap-2">
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                data-testid={`webhook-edit-${hook.id}`}
                                                onClick={() => startEdit(hook)}
                                            >
                                                Edit
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                data-testid={`webhook-ping-${hook.id}`}
                                                disabled={pingMutation.isPending}
                                                onClick={() => {
                                                    setFormError(null);
                                                    pingMutation.mutate(hook.id);
                                                }}
                                            >
                                                Send test
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                                data-testid={`webhook-delete-${hook.id}`}
                                                onClick={() =>
                                                    setPendingDelete({
                                                        id: hook.id,
                                                        url: hook.url,
                                                    })
                                                }
                                            >
                                                Delete
                                            </Button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            <section className="space-y-3" aria-labelledby="webhook-form-heading">
                <h2
                    id="webhook-form-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    {editingId ? "Edit webhook" : "Create webhook"}
                </h2>
                <form
                    className="border-border bg-panel grid max-w-xl gap-3 rounded-md border p-4"
                    onSubmit={onSubmit}
                    data-testid={
                        editingId ? "webhook-edit-form" : "webhook-create-form"
                    }
                >
                    <SettingsField label="URL" htmlFor={urlId} error={fieldErrors.url}>
                        <input
                            id={urlId}
                            name="url"
                            type="url"
                            required
                            value={form.url}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    url: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.url)}
                            className={settingsInputClass(fieldErrors.url)}
                            data-testid="webhook-url"
                        />
                    </SettingsField>
                    <fieldset className="space-y-2">
                        <legend className="text-muted-foreground text-xs tracking-wide uppercase">
                            Events
                        </legend>
                        <div className="grid gap-1.5 sm:grid-cols-2">
                            {WEBHOOK_EVENT_OPTIONS.map((event) => (
                                <label
                                    key={event}
                                    className="flex items-center gap-2 text-sm"
                                >
                                    <input
                                        type="checkbox"
                                        name="events"
                                        value={event}
                                        checked={form.events.includes(event)}
                                        onChange={() => toggleEvent(event)}
                                        data-testid={`webhook-event-${event}`}
                                    />
                                    <span className="font-mono text-[12px]">
                                        {event}
                                    </span>
                                </label>
                            ))}
                        </div>
                        {fieldErrors.events ? (
                            <p className="text-status-error text-xs" role="alert">
                                {fieldErrors.events}
                            </p>
                        ) : null}
                    </fieldset>
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={form.active}
                            data-testid="webhook-active"
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    active: event.target.checked,
                                }))
                            }
                        />
                        Active
                    </label>
                    <SettingsField
                        label={editingId ? "New secret" : "Secret"}
                        htmlFor={secretId}
                        error={fieldErrors.secret}
                        hint={
                            editingId
                                ? "Leave blank to keep the current secret."
                                : "Optional. Write-only; never shown again."
                        }
                    >
                        <input
                            id={secretId}
                            name="secret"
                            type="password"
                            autoComplete="new-password"
                            value={form.secret}
                            disabled={form.clear_secret}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    secret: e.target.value,
                                }))
                            }
                            className={settingsInputClass(fieldErrors.secret)}
                            data-testid="webhook-secret"
                        />
                    </SettingsField>
                    {editingId ? (
                        <label className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={form.clear_secret}
                                data-testid="webhook-clear-secret"
                                onChange={(event) =>
                                    setForm((prev) => ({
                                        ...prev,
                                        clear_secret: event.target.checked,
                                        secret: event.target.checked ? "" : prev.secret,
                                    }))
                                }
                            />
                            Clear secret (use server default)
                        </label>
                    ) : null}
                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="webhook-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                        <Button
                            type="submit"
                            size="sm"
                            disabled={saving}
                            data-testid={
                                editingId
                                    ? "webhook-edit-submit"
                                    : "webhook-create-submit"
                            }
                        >
                            {saving
                                ? "Saving…"
                                : editingId
                                  ? "Save webhook"
                                  : "Create webhook"}
                        </Button>
                        {editingId ? (
                            <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                data-testid="webhook-edit-cancel"
                                onClick={cancelEdit}
                            >
                                Cancel
                            </Button>
                        ) : null}
                    </div>
                </form>
            </section>

            <ConfirmDialog
                open={pendingDelete !== null}
                title="Delete webhook"
                sentence={pendingDelete ? `Delete webhook ${pendingDelete.url}?` : ""}
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
