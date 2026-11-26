import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId, useState, type FormEvent } from "react";

import { createOwner, fetchOwners, patchOwner, type Owner } from "@/api/agents";
import { TesseraApiError, type FieldErrors } from "@/api/client";
import { ownersKeys, settingsKeys } from "@/api/keys";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { RelativeTime } from "@/components/RelativeTime";
import { SettingsField } from "@/components/settings/SettingsField";
import { Button } from "@/components/ui/button";
import { settingsInputClass } from "@/lib/settings-form";
import { cn } from "@/lib/utils";

type FormState = {
    type: "user" | "team";
    email: string;
    display_name: string;
};

const EMPTY_FORM: FormState = {
    type: "user",
    email: "",
    display_name: "",
};

/**
 * Owners directory: list, create, rename, activate / deactivate.
 */
export function OwnersSettings() {
    const queryClient = useQueryClient();
    const typeId = useId();
    const emailId = useId();
    const nameId = useId();

    const [form, setForm] = useState<FormState>(EMPTY_FORM);
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);
    const [flash, setFlash] = useState<string | null>(null);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editName, setEditName] = useState("");

    const listQuery = useQuery({
        queryKey: settingsKeys.owners(),
        queryFn: () => fetchOwners({ limit: 200 }),
    });

    const invalidateOwners = async () => {
        await queryClient.invalidateQueries({ queryKey: settingsKeys.owners() });
        await queryClient.invalidateQueries({ queryKey: ownersKeys.all });
    };

    const createMutation = useMutation({
        mutationFn: createOwner,
        onSuccess: async () => {
            setForm(EMPTY_FORM);
            setFieldErrors({});
            setFormError(null);
            setFlash("Owner created");
            await invalidateOwners();
        },
    });

    const patchMutation = useMutation({
        mutationFn: (input: {
            ownerId: string;
            body: { display_name?: string; active?: boolean };
        }) => patchOwner(input.ownerId, input.body),
        onSuccess: async () => {
            setEditingId(null);
            setEditName("");
            setFlash("Owner updated");
            await invalidateOwners();
        },
        onError: (err) => {
            setFormError(
                err instanceof TesseraApiError ? err.message : "Could not update owner",
            );
        },
    });

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        setFlash(null);
        createMutation.mutate(
            {
                type: form.type,
                email: form.email.trim(),
                display_name: form.display_name.trim(),
            },
            {
                onError: (err) => {
                    if (!(err instanceof TesseraApiError)) {
                        setFormError("Could not create owner");
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
                title="Could not load owners"
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
        <div className="space-y-8" data-testid="settings-owners">
            {flash ? (
                <p className="text-muted-foreground text-sm" role="status">
                    {flash}
                </p>
            ) : null}

            <section className="space-y-3" aria-labelledby="owners-create-heading">
                <h2
                    id="owners-create-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    Create owner
                </h2>
                <form
                    className="border-border bg-panel grid max-w-xl gap-3 rounded-md border p-4"
                    onSubmit={onSubmit}
                    data-testid="owners-create-form"
                >
                    <SettingsField
                        label="Type"
                        htmlFor={typeId}
                        error={fieldErrors.type}
                    >
                        <select
                            id={typeId}
                            name="type"
                            value={form.type}
                            data-testid="owners-create-type"
                            className={settingsInputClass(fieldErrors.type)}
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    type: event.target.value as FormState["type"],
                                }))
                            }
                        >
                            <option value="user">User</option>
                            <option value="team">Team</option>
                        </select>
                    </SettingsField>
                    <SettingsField
                        label="Email"
                        htmlFor={emailId}
                        error={fieldErrors.email}
                    >
                        <input
                            id={emailId}
                            name="email"
                            type="email"
                            required
                            value={form.email}
                            data-testid="owners-create-email"
                            className={settingsInputClass(fieldErrors.email)}
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    email: event.target.value,
                                }))
                            }
                        />
                    </SettingsField>
                    <SettingsField
                        label="Display name"
                        htmlFor={nameId}
                        error={fieldErrors.display_name}
                    >
                        <input
                            id={nameId}
                            name="display_name"
                            required
                            value={form.display_name}
                            data-testid="owners-create-name"
                            className={settingsInputClass(fieldErrors.display_name)}
                            onChange={(event) =>
                                setForm((prev) => ({
                                    ...prev,
                                    display_name: event.target.value,
                                }))
                            }
                        />
                    </SettingsField>
                    {formError ? (
                        <p className="text-status-error-fg text-sm" role="alert">
                            {formError}
                        </p>
                    ) : null}
                    <div>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={createMutation.isPending}
                            data-testid="owners-create-submit"
                        >
                            {createMutation.isPending ? "Creating…" : "Create owner"}
                        </Button>
                    </div>
                </form>
            </section>

            <section className="space-y-3" aria-labelledby="owners-list-heading">
                <h2
                    id="owners-list-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    Directory
                </h2>
                {listQuery.isPending ? (
                    <p className="text-muted-foreground text-sm">Loading…</p>
                ) : null}
                {!listQuery.isPending && items.length === 0 ? (
                    <EmptyState
                        title="No owners yet"
                        body="Create a user or team so agents can be assigned."
                        className="border-border bg-panel rounded-md border py-8"
                    />
                ) : null}
                {items.length > 0 ? (
                    <ul className="divide-border border-border bg-panel divide-y rounded-md border">
                        {items.map((owner) => (
                            <OwnerRow
                                key={owner.id}
                                owner={owner}
                                editing={editingId === owner.id}
                                editName={editName}
                                busy={patchMutation.isPending}
                                onEditStart={() => {
                                    setEditingId(owner.id);
                                    setEditName(owner.display_name);
                                    setFormError(null);
                                    setFlash(null);
                                }}
                                onEditCancel={() => {
                                    setEditingId(null);
                                    setEditName("");
                                }}
                                onEditNameChange={setEditName}
                                onSaveName={() => {
                                    const trimmed = editName.trim();
                                    if (!trimmed || trimmed === owner.display_name) {
                                        setEditingId(null);
                                        return;
                                    }
                                    patchMutation.mutate({
                                        ownerId: owner.id,
                                        body: { display_name: trimmed },
                                    });
                                }}
                                onToggleActive={() => {
                                    setFormError(null);
                                    setFlash(null);
                                    patchMutation.mutate({
                                        ownerId: owner.id,
                                        body: { active: !owner.active },
                                    });
                                }}
                            />
                        ))}
                    </ul>
                ) : null}
            </section>
        </div>
    );
}

type OwnerRowProps = {
    owner: Owner;
    editing: boolean;
    editName: string;
    busy: boolean;
    onEditStart: () => void;
    onEditCancel: () => void;
    onEditNameChange: (value: string) => void;
    onSaveName: () => void;
    onToggleActive: () => void;
};

function OwnerRow({
    owner,
    editing,
    editName,
    busy,
    onEditStart,
    onEditCancel,
    onEditNameChange,
    onSaveName,
    onToggleActive,
}: OwnerRowProps) {
    return (
        <li
            className={cn(
                "flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between",
                !owner.active && "opacity-70",
            )}
            data-testid={`owner-row-${owner.id}`}
        >
            <div className="min-w-0 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                    {editing ? (
                        <input
                            value={editName}
                            aria-label="Display name"
                            data-testid={`owner-edit-name-${owner.id}`}
                            className={settingsInputClass()}
                            onChange={(event) => onEditNameChange(event.target.value)}
                        />
                    ) : (
                        <p className="text-foreground truncate text-sm font-medium">
                            {owner.display_name}
                        </p>
                    )}
                    <span className="border-border bg-muted text-muted-foreground inline-flex rounded-md border px-1.5 py-0.5 text-[10px] tracking-wide uppercase">
                        {owner.type}
                    </span>
                    <span
                        className={cn(
                            "inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
                            owner.active
                                ? "bg-status-ok-bg text-status-ok-fg border-status-ok/40"
                                : "bg-muted text-muted-foreground border-border",
                        )}
                        data-testid={`owner-active-${owner.id}`}
                    >
                        {owner.active ? "Active" : "Inactive"}
                    </span>
                </div>
                <p className="text-muted-foreground truncate text-xs">{owner.email}</p>
                <p className="text-muted-foreground text-xs">
                    Created <RelativeTime value={owner.created_at} />
                </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <Link
                    to="/agents"
                    search={{ owner_id: owner.id }}
                    className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                    data-testid={`owner-agents-link-${owner.id}`}
                >
                    View agents
                </Link>
                {editing ? (
                    <>
                        <Button
                            type="button"
                            size="sm"
                            disabled={busy}
                            data-testid={`owner-save-${owner.id}`}
                            onClick={onSaveName}
                        >
                            Save
                        </Button>
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={onEditCancel}
                        >
                            Cancel
                        </Button>
                    </>
                ) : (
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        data-testid={`owner-rename-${owner.id}`}
                        onClick={onEditStart}
                    >
                        Rename
                    </Button>
                )}
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    data-testid={`owner-toggle-${owner.id}`}
                    onClick={onToggleActive}
                >
                    {owner.active ? "Deactivate" : "Activate"}
                </Button>
            </div>
        </li>
    );
}
