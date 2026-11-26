import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId, useState, type FormEvent } from "react";

import { TesseraApiError, type FieldErrors } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import { fetchBaselines, putBaselineYaml } from "@/api/settings";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SettingsField } from "@/components/settings/SettingsField";
import { settingsInputClass } from "@/lib/settings-form";
import { Button } from "@/components/ui/button";

/**
 * Baselines list + upload form. Detail (YAML / replace / delete) is a nested route.
 */
export function BaselinesSettings() {
    const queryClient = useQueryClient();
    const nameId = useId();
    const fileId = useId();

    const [name, setName] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [formError, setFormError] = useState<string | null>(null);

    const listQuery = useQuery({
        queryKey: settingsKeys.baselines({ limit: 100 }),
        queryFn: () => fetchBaselines({ limit: 100 }),
    });

    const uploadMutation = useMutation({
        mutationFn: async (input: { name: string; yaml: string }) =>
            putBaselineYaml(input.name, input.yaml),
        onSuccess: async (_yaml, variables) => {
            setName("");
            setFile(null);
            setFieldErrors({});
            setFormError(null);
            await queryClient.invalidateQueries({
                queryKey: [...settingsKeys.all, "baselines"],
            });
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.baseline(variables.name),
            });
        },
    });

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        setFieldErrors({});
        setFormError(null);
        const trimmed = name.trim();
        if (!trimmed) {
            setFieldErrors({ name: "name is required" });
            return;
        }
        if (!file) {
            setFieldErrors({ file: "file is required" });
            return;
        }
        void file.text().then(
            (yaml) => {
                uploadMutation.mutate(
                    { name: trimmed, yaml },
                    {
                        onError: (err) => {
                            if (!(err instanceof TesseraApiError)) {
                                setFormError("Could not upload baseline");
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
            },
            () => setFormError("Could not read file"),
        );
    };

    if (listQuery.isError) {
        return (
            <ErrorState
                title="Could not load baselines"
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
        <div className="space-y-8" data-testid="settings-baselines">
            {items.length === 0 && !listQuery.isLoading ? (
                <EmptyState title="No baselines" body="Upload a baseline YAML below." />
            ) : (
                <ul className="space-y-1" data-testid="baselines-list">
                    {items.map((row) => (
                        <li key={row.name}>
                            <Link
                                to="/settings/baselines/$name"
                                params={{ name: row.name }}
                                search={(prev) => prev}
                                className="text-foreground hover:underline"
                                data-testid={`baseline-link-${row.name}`}
                            >
                                {row.name}
                            </Link>
                        </li>
                    ))}
                </ul>
            )}

            <section className="space-y-4" aria-labelledby="upload-baseline-heading">
                <h2
                    id="upload-baseline-heading"
                    className="text-foreground text-base font-semibold"
                >
                    Upload baseline
                </h2>
                <form
                    className="grid max-w-xl gap-3"
                    onSubmit={onSubmit}
                    data-testid="baseline-upload-form"
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
                            data-testid="baseline-name"
                        />
                    </SettingsField>
                    <SettingsField
                        label="File"
                        htmlFor={fileId}
                        error={fieldErrors.file}
                    >
                        <input
                            id={fileId}
                            name="file"
                            type="file"
                            accept=".yaml,.yml,text/yaml,application/yaml"
                            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                            className="text-sm"
                            data-testid="baseline-file"
                        />
                    </SettingsField>
                    {formError ? (
                        <p
                            className="text-status-error text-sm"
                            role="alert"
                            data-testid="baseline-form-error"
                        >
                            {formError}
                        </p>
                    ) : null}
                    <div>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={uploadMutation.isPending}
                            data-testid="baseline-upload-submit"
                        >
                            {uploadMutation.isPending
                                ? "Uploading…"
                                : "Upload baseline"}
                        </Button>
                    </div>
                </form>
            </section>
        </div>
    );
}
