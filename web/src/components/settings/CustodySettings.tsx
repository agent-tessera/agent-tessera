import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { TesseraApiError, type FieldErrors } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import {
    createCustodySecret,
    deleteCustodySecret,
    fetchCustodySecrets,
    updateCustodySecret,
    type CustodySecret,
} from "@/api/settings";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SettingsField } from "@/components/settings/SettingsField";
import { settingsInputClass } from "@/lib/settings-form";
import { Button } from "@/components/ui/button";
import { Mono } from "@/components/Mono";

type CreateForm = {
    name: string;
    provider: string;
    secret: string;
};

const EMPTY_CREATE: CreateForm = {
    name: "",
    provider: "",
    secret: "",
};

/**
 * Custody settings: create / replace / delete. Keys use password inputs;
 * plaintext and ciphertext never appear in responses or the DOM.
 */
export function CustodySettings() {
    const queryClient = useQueryClient();
    const nameId = useId();
    const providerId = useId();
    const secretId = useId();

    const [form, setForm] = useState<CreateForm>(EMPTY_CREATE);
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [replaceDrafts, setReplaceDrafts] = useState<Record<string, string>>({});
    const [replaceErrors, setReplaceErrors] = useState<Record<string, string>>({});
    const [pendingDelete, setPendingDelete] = useState<CustodySecret | null>(null);

    const listQuery = useQuery({
        queryKey: settingsKeys.custody(),
        queryFn: fetchCustodySecrets,
    });

    const invalidate = async () => {
        await queryClient.invalidateQueries({
            queryKey: settingsKeys.custody(),
        });
    };

    const createMutation = useMutation({
        mutationFn: createCustodySecret,
        onSuccess: async () => {
            setForm(EMPTY_CREATE);
            setFieldErrors({});
            setFormError(null);
            await invalidate();
        },
    });

    const replaceMutation = useMutation({
        mutationFn: (input: { id: string; secret: string }) =>
            updateCustodySecret(input.id, { secret: input.secret }),
        onSuccess: async (_row, variables) => {
            setReplaceDrafts((prev) => {
                const next = { ...prev };
                delete next[variables.id];
                return next;
            });
            setReplaceErrors((prev) => {
                const next = { ...prev };
                delete next[variables.id];
                return next;
            });
            await invalidate();
        },
    });

    const deleteMutation = useMutation({
        mutationFn: (id: string) => deleteCustodySecret(id),
        onSuccess: async () => {
            setPendingDelete(null);
            await invalidate();
        },
    });

    const onCreate = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        createMutation.mutate(
            {
                name: form.name.trim(),
                provider: form.provider.trim(),
                secret: form.secret,
            },
            {
                onError: (err) => {
                    if (!(err instanceof TesseraApiError)) {
                        setFormError("Could not store key");
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

    const onReplace = (row: CustodySecret) => {
        const secret = replaceDrafts[row.id] ?? "";
        if (!secret) {
            setReplaceErrors((prev) => ({
                ...prev,
                [row.id]: "key is required",
            }));
            return;
        }
        replaceMutation.mutate(
            { id: row.id, secret },
            {
                onError: (err) => {
                    setReplaceErrors((prev) => ({
                        ...prev,
                        [row.id]:
                            err instanceof TesseraApiError
                                ? err.message
                                : "Could not replace key",
                    }));
                },
            },
        );
    };

    if (listQuery.isError) {
        return (
            <ErrorState
                title="Could not load custody secrets"
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
        <div className="space-y-8" data-testid="settings-custody">
            {items.length === 0 && !listQuery.isLoading ? (
                <EmptyState
                    title="No provider secrets"
                    body="Store a key below. Plaintext never appears on this page."
                />
            ) : (
                <div className="border-border overflow-x-auto rounded-md border">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-muted/40 text-muted-foreground text-xs tracking-wide uppercase">
                            <tr>
                                <th className="px-3 py-2 font-medium">Name</th>
                                <th className="px-3 py-2 font-medium">Provider</th>
                                <th className="px-3 py-2 font-medium">Id</th>
                                <th className="px-3 py-2 font-medium" />
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((row) => (
                                <tr
                                    key={row.id}
                                    className="border-border border-t"
                                    data-testid={`custody-row-${row.id}`}
                                    data-name={row.name}
                                    data-provider={row.provider}
                                >
                                    <td className="px-3 py-2">{row.name}</td>
                                    <td className="px-3 py-2">{row.provider}</td>
                                    <td className="px-3 py-2">
                                        <Mono>{row.id}</Mono>
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex flex-wrap items-end justify-end gap-2">
                                            <div className="space-y-1">
                                                <label
                                                    className="sr-only"
                                                    htmlFor={`replace-${row.id}`}
                                                >
                                                    New key for {row.name}
                                                </label>
                                                <input
                                                    id={`replace-${row.id}`}
                                                    type="password"
                                                    autoComplete="new-password"
                                                    placeholder="New key"
                                                    value={replaceDrafts[row.id] ?? ""}
                                                    onChange={(e) =>
                                                        setReplaceDrafts((prev) => ({
                                                            ...prev,
                                                            [row.id]: e.target.value,
                                                        }))
                                                    }
                                                    className={settingsInputClass(
                                                        replaceErrors[row.id],
                                                    )}
                                                    data-testid={`custody-replace-input-${row.id}`}
                                                />
                                                {replaceErrors[row.id] ? (
                                                    <p
                                                        className="text-status-error text-xs"
                                                        role="alert"
                                                    >
                                                        {replaceErrors[row.id]}
                                                    </p>
                                                ) : null}
                                            </div>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                data-testid={`custody-replace-${row.id}`}
                                                disabled={replaceMutation.isPending}
                                                onClick={() => onReplace(row)}
                                            >
                                                Replace key
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                                data-testid={`custody-delete-${row.id}`}
                                                onClick={() => setPendingDelete(row)}
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

            <section className="space-y-4" aria-labelledby="create-custody-heading">
                <h2
                    id="create-custody-heading"
                    className="text-foreground text-base font-semibold"
                >
                    Store key
                </h2>
                <form
                    className="grid max-w-xl gap-3"
                    onSubmit={onCreate}
                    data-testid="custody-create-form"
                    autoComplete="off"
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
                            data-testid="custody-name"
                        />
                    </SettingsField>
                    <SettingsField
                        label="Provider"
                        htmlFor={providerId}
                        error={fieldErrors.provider}
                    >
                        <input
                            id={providerId}
                            name="provider"
                            required
                            value={form.provider}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    provider: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.provider)}
                            className={settingsInputClass(fieldErrors.provider)}
                            data-testid="custody-provider"
                        />
                    </SettingsField>
                    <SettingsField
                        label="Key"
                        htmlFor={secretId}
                        error={fieldErrors.secret}
                    >
                        <input
                            id={secretId}
                            name="secret"
                            type="password"
                            autoComplete="new-password"
                            required
                            value={form.secret}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    secret: e.target.value,
                                }))
                            }
                            aria-invalid={Boolean(fieldErrors.secret)}
                            className={settingsInputClass(fieldErrors.secret)}
                            data-testid="custody-secret"
                        />
                    </SettingsField>
                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="custody-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}
                    <div>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={createMutation.isPending}
                            data-testid="custody-create-submit"
                        >
                            {createMutation.isPending ? "Storing…" : "Create"}
                        </Button>
                    </div>
                </form>
            </section>

            <ConfirmDialog
                open={pendingDelete !== null}
                title="Delete custody secret"
                sentence={
                    pendingDelete ? `Delete custody secret ${pendingDelete.name}?` : ""
                }
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
