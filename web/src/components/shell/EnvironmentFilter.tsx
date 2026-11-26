import { getRouteApi, useRouter } from "@tanstack/react-router";

import { ENVIRONMENT_OPTIONS, environmentLabel } from "@/lib/environment";
import { cn } from "@/lib/utils";

const consoleRoute = getRouteApi("/_console");

export type EnvironmentFilterProps = {
    className?: string;
};

/** Top-bar environment filter persisted in the `environment` search param. */
export function EnvironmentFilter({ className }: EnvironmentFilterProps) {
    const router = useRouter();
    const search = consoleRoute.useSearch();
    const current = search.environment;

    return (
        <label
            className={cn(
                "text-muted-foreground flex items-center gap-2 text-xs",
                className,
            )}
        >
            <span className="sr-only">Environment</span>
            <select
                className="border-border bg-panel text-foreground h-8 rounded-md border px-2 text-sm"
                aria-label="Environment filter"
                value={current ?? ""}
                onChange={(event) => {
                    const value = event.target.value;
                    const params = new URLSearchParams(
                        router.state.location.searchStr.replace(/^\?/, ""),
                    );
                    if (value) {
                        params.set("environment", value);
                    } else {
                        params.delete("environment");
                    }
                    const qs = params.toString();
                    router.history.replace(
                        `${router.state.location.pathname}${qs ? `?${qs}` : ""}`,
                    );
                }}
            >
                <option value="">{environmentLabel(undefined)}</option>
                {ENVIRONMENT_OPTIONS.map((env) => (
                    <option key={env} value={env}>
                        {env}
                    </option>
                ))}
                {current &&
                !ENVIRONMENT_OPTIONS.includes(
                    current as (typeof ENVIRONMENT_OPTIONS)[number],
                ) ? (
                    <option value={current}>{current}</option>
                ) : null}
            </select>
        </label>
    );
}
