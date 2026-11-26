/** Environment filter helpers kept in the console URL search params. */

/** Common environment slugs offered in the shell filter. */
export const ENVIRONMENT_OPTIONS = ["dev", "staging", "prod"] as const;

export type EnvironmentOption = (typeof ENVIRONMENT_OPTIONS)[number];

export type EnvironmentSearch = {
    environment?: string;
};

/**
 * Parse the `environment` search param for the console shell.
 *
 * @param search - Raw route search object.
 * @returns Validated search; omitted when unset or empty.
 */
export function parseEnvironmentSearch(
    search: Record<string, unknown>,
): EnvironmentSearch {
    const raw = search.environment;
    if (typeof raw !== "string") {
        return {};
    }
    const environment = raw.trim();
    if (!environment) {
        return {};
    }
    return { environment };
}

/**
 * Label for the current environment filter selection.
 *
 * @param environment - Selected slug, or `undefined` for all.
 */
export function environmentLabel(environment: string | undefined): string {
    return environment ?? "All environments";
}
