import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState, type FormEvent } from "react";

import { createRegistrationToken, fetchAgentInstances } from "@/api/agents";
import { agentsKeys } from "@/api/keys";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";
import {
    CONNECT_PYTHON_SNIPPET,
    connectEnvSnippet,
    firstConnectedShortId,
} from "@/lib/agent-connect";
import { cn } from "@/lib/utils";

export type AgentConnectProps = {
    agentId: string;
};

const POLL_MS = 2000;

async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/**
 * M11 Connect tab: mint a registration token once and poll for first instance.
 */
export function AgentConnect({ agentId }: AgentConnectProps) {
    const reusableId = useId();
    const expiresId = useId();
    const [reusable, setReusable] = useState(false);
    const [expiresIn, setExpiresIn] = useState("");
    const [formError, setFormError] = useState<string | null>(null);
    const [issuedToken, setIssuedToken] = useState<string | null>(null);
    const [copied, setCopied] = useState<"token" | "env" | "python" | null>(null);

    const instancesQuery = useQuery({
        queryKey: agentsKeys.instances(agentId),
        queryFn: () => fetchAgentInstances(agentId, { limit: 100 }),
        refetchInterval: (query) => {
            const short = firstConnectedShortId(query.state.data?.items ?? []);
            return short ? false : POLL_MS;
        },
    });

    const createMutation = useMutation({
        mutationFn: (input: { reusable: boolean; expires_in: number | null }) =>
            createRegistrationToken(agentId, {
                reusable: input.reusable,
                expires_in: input.expires_in,
            }),
        onSuccess: (created) => {
            setFormError(null);
            setIssuedToken(created.token);
        },
        onError: (error) => {
            setFormError(
                error instanceof Error ? error.message : "Could not create token",
            );
        },
    });

    const connectedShort = useMemo(
        () => firstConnectedShortId(instancesQuery.data?.items ?? []),
        [instancesQuery.data],
    );

    const serverUrl = useMemo(() => {
        if (typeof window === "undefined") {
            return "";
        }
        return window.location.origin.replace(/\/$/, "");
    }, []);

    const envSnippet = useMemo(() => {
        if (!issuedToken) {
            return "";
        }
        return connectEnvSnippet(serverUrl, issuedToken);
    }, [issuedToken, serverUrl]);

    const markCopied = (kind: "token" | "env" | "python") => {
        setCopied(kind);
        window.setTimeout(() => setCopied(null), 1200);
    };

    const submit = (event: FormEvent) => {
        event.preventDefault();
        setFormError(null);

        let lifetime: number | null = null;
        const trimmed = expiresIn.trim();
        if (trimmed !== "") {
            const parsed = Number(trimmed);
            if (!Number.isInteger(parsed) || parsed <= 0) {
                setFormError("expires_in must be a positive integer number of seconds");
                return;
            }
            lifetime = parsed;
        }
        if (reusable && lifetime === null) {
            setFormError("reusable tokens require expires_in");
            return;
        }

        createMutation.mutate({
            reusable,
            expires_in: lifetime,
        });
    };

    if (instancesQuery.isError) {
        return (
            <div data-testid="agent-connect">
                <ErrorState
                    title="Could not load connection status"
                    message={
                        instancesQuery.error instanceof Error
                            ? instancesQuery.error.message
                            : "Request failed"
                    }
                    onRetry={() => void instancesQuery.refetch()}
                />
            </div>
        );
    }

    return (
        <div className="space-y-8" data-testid="agent-connect">
            <section className="space-y-3" aria-labelledby="connect-token-heading">
                <div className="space-y-1">
                    <h2 id="connect-token-heading" className="text-sm font-semibold">
                        Registration token
                    </h2>
                    <p className="text-muted-foreground text-sm">
                        Mint a short-lived credential for the agent runtime. The
                        plaintext token is shown once.
                    </p>
                </div>

                {issuedToken ? (
                    <div className="space-y-4" data-testid="connect-token-once">
                        <p className="text-sm">
                            This token is shown once. Copy the values below into the
                            agent environment.
                        </p>

                        <SnippetBlock
                            title="Token"
                            testId="registration-token"
                            copyTestId="connect-copy-token"
                            text={issuedToken}
                            copied={copied === "token"}
                            onCopy={() => {
                                void copyText(issuedToken).then((ok) => {
                                    if (ok) {
                                        markCopied("token");
                                    }
                                });
                            }}
                        />

                        <SnippetBlock
                            title="Environment"
                            testId="env-snippet"
                            copyTestId="connect-copy-env"
                            text={envSnippet}
                            copied={copied === "env"}
                            onCopy={() => {
                                void copyText(envSnippet).then((ok) => {
                                    if (ok) {
                                        markCopied("env");
                                    }
                                });
                            }}
                        />

                        <SnippetBlock
                            title="Python"
                            testId="python-snippet"
                            copyTestId="connect-copy-python"
                            text={CONNECT_PYTHON_SNIPPET}
                            copied={copied === "python"}
                            onCopy={() => {
                                void copyText(CONNECT_PYTHON_SNIPPET).then((ok) => {
                                    if (ok) {
                                        markCopied("python");
                                    }
                                });
                            }}
                        />

                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            data-testid="connect-back"
                            onClick={() => {
                                setIssuedToken(null);
                                setFormError(null);
                            }}
                        >
                            Back to Connect
                        </Button>
                    </div>
                ) : (
                    <form
                        className="space-y-3"
                        onSubmit={submit}
                        data-testid="connect-token-form"
                        noValidate
                    >
                        <label
                            htmlFor={reusableId}
                            className="flex items-center gap-2 text-sm"
                        >
                            <input
                                id={reusableId}
                                name="reusable"
                                type="checkbox"
                                checked={reusable}
                                data-testid="connect-reusable"
                                onChange={(event) => setReusable(event.target.checked)}
                            />
                            Reusable
                        </label>

                        <div className="space-y-1">
                            <label htmlFor={expiresId} className="text-sm font-medium">
                                Expires in seconds
                            </label>
                            <input
                                id={expiresId}
                                name="expires_in"
                                type="text"
                                inputMode="numeric"
                                value={expiresIn}
                                data-testid="connect-expires-in"
                                placeholder={
                                    reusable ? "Required when reusable" : "Optional"
                                }
                                className={cn(
                                    "border-border bg-background focus-visible:ring-ring w-full max-w-xs rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none",
                                )}
                                onChange={(event) => setExpiresIn(event.target.value)}
                                autoComplete="off"
                                spellCheck={false}
                            />
                        </div>

                        {formError ? (
                            <p
                                className="text-status-error-fg text-sm"
                                role="alert"
                                data-testid="connect-error"
                            >
                                {formError}
                            </p>
                        ) : null}

                        <Button
                            type="submit"
                            size="sm"
                            data-testid="connect-create"
                            disabled={createMutation.isPending}
                        >
                            {createMutation.isPending
                                ? "Creating…"
                                : "Create registration token"}
                        </Button>
                    </form>
                )}
            </section>

            <section className="space-y-2" aria-labelledby="connect-status-heading">
                <h2 id="connect-status-heading" className="text-sm font-semibold">
                    Connection
                </h2>
                <div
                    className="text-sm"
                    data-testid="connection-status"
                    data-connected={connectedShort ? "true" : "false"}
                    aria-live="polite"
                >
                    {instancesQuery.isPending && !instancesQuery.data ? (
                        <p className="text-muted-foreground">Checking connection…</p>
                    ) : connectedShort ? (
                        <p data-testid="connection-connected">
                            Connected:{" "}
                            <code className="font-mono text-[13px]">
                                {connectedShort}
                            </code>
                        </p>
                    ) : (
                        <p data-testid="connection-waiting">
                            Waiting for the first connection…
                        </p>
                    )}
                </div>
            </section>
        </div>
    );
}

type SnippetBlockProps = {
    title: string;
    text: string;
    testId: string;
    copyTestId: string;
    copied: boolean;
    onCopy: () => void;
};

function SnippetBlock({
    title,
    text,
    testId,
    copyTestId,
    copied,
    onCopy,
}: SnippetBlockProps) {
    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{title}</h3>
                <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    data-testid={copyTestId}
                    onClick={onCopy}
                >
                    {copied ? "Copied" : "Copy"}
                </Button>
            </div>
            <pre
                id={testId}
                data-testid={testId}
                className="border-border bg-muted/40 overflow-x-auto rounded-md border p-3 font-mono text-[13px] break-all whitespace-pre-wrap"
            >
                {text}
            </pre>
        </div>
    );
}
