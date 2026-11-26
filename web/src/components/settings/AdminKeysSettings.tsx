import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { TesseraApiError, type FieldErrors } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import {
    createAdminKey,
    fetchAdminKeys,
    revokeAdminKey,
    type AdminKeyRow,
} from "@/api/settings";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { SettingsField } from "@/components/settings/SettingsField";
import { settingsInputClass } from "@/lib/settings-form";
import { Button } from "@/components/ui/button";

async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/**
 * Admin keys: list, create (secret shown once), revoke (ends sessions).
 */
export function AdminKeysSettings() {
    const queryClient = useQueryClient();
    const nameId = useId();

    const [name, setName] = useState("");
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [issuedToken, setIssuedToken] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const [pendingRevoke, setPendingRevoke] = useState<AdminKeyRow | null>(null);

    const listQuery = useQuery({
        queryKey: settingsKeys.adminKeys(),
        queryFn: fetchAdminKeys,
    });

    const createMutation = useMutation({
        mutationFn: createAdminKey,
        onSuccess: async (created) => {
            setName("");
            setFieldErrors({});
            setFormError(null);
            setIssuedToken(created.token);
            setCopied(false);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.adminKeys(),
            });
        },
    });

    const revokeMutation = useMutation({
        mutationFn: (keyId: string) => revokeAdminKey(keyId),
        onSuccess: async () => {
            setPendingRevoke(null);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.adminKeys(),
            });
        },
    });

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        setIssuedToken(null);
        createMutation.mutate(name.trim(), {
            onError: (err) => {
                if (!(err instanceof TesseraApiError)) {
                    setFormError("Could not create admin key");
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

    if (listQuery.isError) {
        return (
            <ErrorState
                title="Could not load admin keys"
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
        <div className="space-y-8" data-testid="settings-admin-keys">
            {issuedToken ? (
                <section
                    className="border-border bg-muted/30 space-y-3 rounded-md border p-4"
                    role="status"
                    data-testid="admin-key-once"
                >
                    <h2 className="text-foreground text-base font-semibold">
                        New admin key
                    </h2>
                    <p className="text-muted-foreground text-sm">
                        Copy it now. Tessera will not show it again.
                    </p>
                    <p
                        className="font-mono text-sm break-all"
                        data-testid="admin-key-token"
                        id="new-admin-key"
                    >
                        {issuedToken}
                    </p>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-testid="admin-key-copy"
                        onClick={() => {
                            void copyText(issuedToken).then((ok) => {
                                if (!ok) {
                                    return;
                                }
                                setCopied(true);
                                window.setTimeout(() => setCopied(false), 1200);
                            });
                        }}
                    >
                        {copied ? "Copied" : "Copy"}
                    </Button>
                </section>
            ) : null}

            {items.length === 0 && !listQuery.isLoading ? (
                <EmptyState
                    title="No admin keys"
                    body="Create a key below. The secret is shown once."
                />
            ) : (
                <div className="border-border overflow-x-auto rounded-md border">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-muted/40 text-muted-foreground text-xs tracking-wide uppercase">
                            <tr>
                                <th className="px-3 py-2 font-medium">Name</th>
                                <th className="px-3 py-2 font-medium">Id</th>
                                <th className="px-3 py-2 font-medium">Created</th>
                                <th className="px-3 py-2 font-medium">Last used</th>
                                <th className="px-3 py-2 font-medium">Status</th>
                                <th className="px-3 py-2 font-medium" />
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((key) => (
                                <tr
                                    key={key.id}
                                    className="border-border border-t"
                                    data-testid={`admin-key-row-${key.id}`}
                                    data-revoked={key.revoked ? "true" : "false"}
                                >
                                    <td className="px-3 py-2">{key.name}</td>
                                    <td className="px-3 py-2">
                                        <Mono>{key.id}</Mono>
                                    </td>
                                    <td className="px-3 py-2">
                                        <RelativeTime value={key.created_at} />
                                    </td>
                                    <td className="px-3 py-2">
                                        {key.last_used_at ? (
                                            <RelativeTime value={key.last_used_at} />
                                        ) : (
                                            "—"
                                        )}
                                    </td>
                                    <td className="px-3 py-2">
                                        <span
                                            className={
                                                key.revoked
                                                    ? "bg-status-revoked-bg text-status-revoked-fg border-status-revoked/40 inline-flex rounded-md border px-1.5 py-0.5 text-xs font-medium"
                                                    : "bg-status-ok-bg text-status-ok-fg border-status-ok/40 inline-flex rounded-md border px-1.5 py-0.5 text-xs font-medium"
                                            }
                                        >
                                            {key.revoked ? "revoked" : "active"}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2 text-right">
                                        {!key.revoked ? (
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                                data-testid={`admin-key-revoke-${key.id}`}
                                                onClick={() => setPendingRevoke(key)}
                                            >
                                                Revoke
                                            </Button>
                                        ) : null}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            <section className="space-y-4" aria-labelledby="create-admin-key-heading">
                <h2
                    id="create-admin-key-heading"
                    className="text-foreground text-base font-semibold"
                >
                    Create admin key
                </h2>
                <form
                    className="grid max-w-xl gap-3"
                    onSubmit={onSubmit}
                    data-testid="admin-key-create-form"
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
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            aria-invalid={Boolean(fieldErrors.name)}
                            className={settingsInputClass(fieldErrors.name)}
                            data-testid="admin-key-name"
                        />
                    </SettingsField>
                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="admin-key-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}
                    <div>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={createMutation.isPending}
                            data-testid="admin-key-create-submit"
                        >
                            {createMutation.isPending ? "Creating…" : "Create key"}
                        </Button>
                    </div>
                </form>
            </section>

            <ConfirmDialog
                open={pendingRevoke !== null}
                title="Revoke admin key"
                sentence={
                    pendingRevoke
                        ? `Revoke admin key ${pendingRevoke.name}? Its sessions will end.`
                        : ""
                }
                confirmLabel="Revoke"
                destructive
                onCancel={() => setPendingRevoke(null)}
                onConfirm={() => {
                    if (pendingRevoke) {
                        revokeMutation.mutate(pendingRevoke.id);
                    }
                }}
            />
        </div>
    );
}
