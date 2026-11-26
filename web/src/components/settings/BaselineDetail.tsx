import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useEffect, useState } from "react";

import { TesseraApiError } from "@/api/client";
import { settingsKeys } from "@/api/keys";
import {
    deleteBaseline,
    dryRunBaselineYaml,
    fetchBaselineAgents,
    fetchBaselineYaml,
    putBaselineYaml,
    validateBaselineYaml,
    type BaselineDryRunItem,
} from "@/api/settings";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";

/** CodeMirror YAML editor — same lazy chunk pattern as the Policy tab. */
const PolicyYamlEditor = lazy(() =>
    import("@/components/agents/PolicyYamlEditor").then((mod) => ({
        default: mod.PolicyYamlEditor,
    })),
);

export type BaselineDetailProps = {
    name: string;
};

/**
 * Baseline detail: CodeMirror YAML, inheritors, validate / dry-run, replace, delete.
 */
export function BaselineDetail({ name }: BaselineDetailProps) {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [draft, setDraft] = useState("");
    const [loaded, setLoaded] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [flash, setFlash] = useState<string | null>(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [dryRunItems, setDryRunItems] = useState<BaselineDryRunItem[] | null>(null);
    const [matchingAgents, setMatchingAgents] = useState<number | null>(null);

    const yamlQuery = useQuery({
        queryKey: settingsKeys.baseline(name),
        queryFn: () => fetchBaselineYaml(name),
    });

    const agentsQuery = useQuery({
        queryKey: [...settingsKeys.baseline(name), "agents"],
        queryFn: () => fetchBaselineAgents(name),
    });

    useEffect(() => {
        if (yamlQuery.data !== undefined && !loaded) {
            setDraft(yamlQuery.data);
            setLoaded(true);
        }
    }, [yamlQuery.data, loaded]);

    useEffect(() => {
        setLoaded(false);
        setDraft("");
        setFormError(null);
        setFlash(null);
        setDryRunItems(null);
        setMatchingAgents(null);
    }, [name]);

    const saveMutation = useMutation({
        mutationFn: (yaml: string) => putBaselineYaml(name, yaml),
        onSuccess: async (stored) => {
            setDraft(stored);
            setFormError(null);
            setFlash("Baseline saved");
            setDryRunItems(null);
            await queryClient.invalidateQueries({
                queryKey: settingsKeys.baseline(name),
            });
            await queryClient.invalidateQueries({
                queryKey: [...settingsKeys.all, "baselines"],
            });
            await queryClient.invalidateQueries({
                queryKey: [...settingsKeys.baseline(name), "agents"],
            });
        },
        onError: (err) => {
            setFormError(
                err instanceof TesseraApiError
                    ? err.message
                    : "Could not save baseline",
            );
        },
    });

    const validateMutation = useMutation({
        mutationFn: () => validateBaselineYaml(name, draft),
        onSuccess: () => {
            setFormError(null);
            setFlash("Baseline YAML is valid");
        },
        onError: (err) => {
            setFlash(null);
            setFormError(
                err instanceof TesseraApiError ? err.message : "Validation failed",
            );
        },
    });

    const dryRunMutation = useMutation({
        mutationFn: () => dryRunBaselineYaml(name, draft),
        onSuccess: (result) => {
            setFormError(null);
            setMatchingAgents(result.matching_agents);
            setDryRunItems(result.items);
            setFlash(
                `Dry-run: ${result.matching_agents} matching agent(s), ${result.items.filter((row) => row.would_change).length} would change`,
            );
        },
        onError: (err) => {
            setFlash(null);
            setFormError(
                err instanceof TesseraApiError ? err.message : "Dry-run failed",
            );
        },
    });

    const deleteMutation = useMutation({
        mutationFn: () => deleteBaseline(name),
        onSuccess: async () => {
            await queryClient.invalidateQueries({
                queryKey: [...settingsKeys.all, "baselines"],
            });
            void navigate({ to: "/settings/baselines", search: (prev) => prev });
        },
        onError: (err) => {
            setConfirmDelete(false);
            setFormError(
                err instanceof TesseraApiError
                    ? err.message
                    : "Could not delete baseline",
            );
        },
    });

    if (yamlQuery.isError) {
        return (
            <ErrorState
                title="Could not load baseline"
                message={
                    yamlQuery.error instanceof Error
                        ? yamlQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void yamlQuery.refetch()}
            />
        );
    }

    const dirty = loaded && draft !== (yamlQuery.data ?? "");
    const inheritors = agentsQuery.data?.items ?? [];

    return (
        <div className="space-y-4" data-testid="settings-baseline-detail">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="space-y-1">
                    <p className="text-muted-foreground text-xs">
                        <Link
                            to="/settings/baselines"
                            search={(prev) => prev}
                            className="hover:text-foreground underline-offset-2 hover:underline"
                        >
                            Baselines
                        </Link>
                        <span aria-hidden className="px-1">
                            /
                        </span>
                        <span className="text-foreground">{name}</span>
                    </p>
                    <h2
                        className="text-foreground text-base font-semibold"
                        data-testid="baseline-detail-name"
                    >
                        {name}
                    </h2>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={validateMutation.isPending || !loaded}
                        data-testid="baseline-validate"
                        onClick={() => validateMutation.mutate()}
                    >
                        {validateMutation.isPending ? "Validating…" : "Validate"}
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={dryRunMutation.isPending || !loaded}
                        data-testid="baseline-dry-run"
                        onClick={() => dryRunMutation.mutate()}
                    >
                        {dryRunMutation.isPending ? "Running…" : "Dry-run"}
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        disabled={!dirty || saveMutation.isPending}
                        data-testid="baseline-replace-save"
                        onClick={() => saveMutation.mutate(draft)}
                    >
                        {saveMutation.isPending ? "Saving…" : "Replace"}
                    </Button>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="border-status-error text-status-error-fg hover:bg-status-error-bg border"
                        data-testid="baseline-delete"
                        onClick={() => setConfirmDelete(true)}
                    >
                        Delete
                    </Button>
                </div>
            </div>

            {flash ? (
                <p
                    className="text-muted-foreground text-sm"
                    role="status"
                    data-testid="baseline-flash"
                >
                    {flash}
                    {matchingAgents != null ? (
                        <span className="sr-only"> matching {matchingAgents}</span>
                    ) : null}
                </p>
            ) : null}

            {formError ? (
                <p
                    className="text-status-error text-sm"
                    role="alert"
                    data-testid="baseline-detail-error"
                >
                    {formError}
                </p>
            ) : null}

            <section className="space-y-2" aria-labelledby="baseline-inheritors-h">
                <h3
                    id="baseline-inheritors-h"
                    className="text-foreground text-sm font-semibold"
                >
                    Who inherits
                </h3>
                {agentsQuery.isPending ? (
                    <p className="text-muted-foreground text-sm">Loading…</p>
                ) : null}
                {agentsQuery.isError ? (
                    <ErrorState
                        title="Could not load inheritors"
                        message={
                            agentsQuery.error instanceof Error
                                ? agentsQuery.error.message
                                : "Request failed"
                        }
                        onRetry={() => void agentsQuery.refetch()}
                    />
                ) : null}
                {!agentsQuery.isPending &&
                !agentsQuery.isError &&
                inheritors.length === 0 ? (
                    <EmptyState
                        title="No matching agents"
                        body="No registered agents match this baseline selector."
                        className="border-border bg-panel rounded-md border py-6"
                    />
                ) : null}
                {inheritors.length > 0 ? (
                    <ul
                        className="border-border bg-panel divide-border divide-y rounded-md border"
                        data-testid="baseline-inheritors"
                    >
                        {inheritors.map((agent) => (
                            <li
                                key={agent.id}
                                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
                            >
                                <Link
                                    to="/agents/$agentId"
                                    params={{ agentId: agent.id }}
                                    className="underline-offset-2 hover:underline"
                                >
                                    {agent.name}
                                </Link>
                                <span className="text-muted-foreground text-xs">
                                    {agent.environment} · {agent.status}
                                </span>
                            </li>
                        ))}
                    </ul>
                ) : null}
            </section>

            {dryRunItems ? (
                <section className="space-y-2" aria-labelledby="baseline-dry-run-h">
                    <h3
                        id="baseline-dry-run-h"
                        className="text-foreground text-sm font-semibold"
                    >
                        Dry-run impact
                    </h3>
                    <ul
                        className="border-border bg-panel divide-border divide-y rounded-md border"
                        data-testid="baseline-dry-run-list"
                    >
                        {dryRunItems.length === 0 ? (
                            <li className="text-muted-foreground px-3 py-2 text-sm">
                                No agents with stored policies.
                            </li>
                        ) : (
                            dryRunItems.map((row) => (
                                <li
                                    key={row.agent_id}
                                    className="space-y-0.5 px-3 py-2 text-sm"
                                    data-testid={`baseline-dry-run-${row.agent_id}`}
                                >
                                    <p className="font-medium">
                                        {row.name}{" "}
                                        <span className="text-muted-foreground font-normal">
                                            · {row.environment}
                                        </span>
                                    </p>
                                    <p className="text-muted-foreground text-xs">
                                        {row.ok
                                            ? row.would_change
                                                ? "Would change effective version"
                                                : "No version change"
                                            : `Error: ${row.error ?? "invalid"}`}
                                    </p>
                                </li>
                            ))
                        )}
                    </ul>
                </section>
            ) : null}

            <div
                className="border-border bg-panel min-h-64 overflow-hidden rounded-md border"
                data-testid="baseline-yaml-editor"
            >
                {yamlQuery.isLoading || !loaded ? (
                    <p className="text-muted-foreground p-3 text-sm">Loading YAML…</p>
                ) : (
                    <Suspense
                        fallback={
                            <p className="text-muted-foreground p-3 text-sm">
                                Loading editor…
                            </p>
                        }
                    >
                        <PolicyYamlEditor value={draft} onChange={setDraft} />
                    </Suspense>
                )}
            </div>

            <ConfirmDialog
                open={confirmDelete}
                title="Delete baseline"
                sentence={`Delete baseline ${name}?`}
                confirmLabel="Delete"
                destructive
                onCancel={() => setConfirmDelete(false)}
                onConfirm={() => deleteMutation.mutate()}
            />
        </div>
    );
}
