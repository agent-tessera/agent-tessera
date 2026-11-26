/**
 * Cookie-authenticated fetch wrapper for `/v1` console APIs.
 *
 * Sends credentials, attaches CSRF on mutating calls (after `GET /v1/me`),
 * redirects on 401, maps 422 `details` to field errors, and surfaces
 * `csrf_failed` as a typed error.
 */

import type { paths } from "./schema";

/** HTTP methods that never require a CSRF header. */
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

/** Tessera JSON error document (`ApiError` / validation handler). */
export type ApiErrorBody = {
    error: {
        code: string;
        message: string;
        details?: Array<{ field: string; reason: string }>;
    };
};

/** Field → reason map produced from a 422 `details` array. */
export type FieldErrors = Record<string, string>;

/**
 * Failed `/v1` response with optional field errors.
 *
 * Thrown for every non-OK response after redirect handling.
 */
export class TesseraApiError extends Error {
    readonly status: number;
    readonly code: string;
    readonly fieldErrors: FieldErrors;

    /**
     * @param status - HTTP status.
     * @param code - Tessera error code (e.g. `csrf_failed`).
     * @param message - Human-readable message.
     * @param fieldErrors - Mapped validation details (422 only).
     */
    constructor(
        status: number,
        code: string,
        message: string,
        fieldErrors: FieldErrors = {},
    ) {
        super(message);
        this.name = "TesseraApiError";
        this.status = status;
        this.code = code;
        this.fieldErrors = fieldErrors;
    }

    /** True when the server rejected a missing or mismatched CSRF token. */
    get isCsrfFailed(): boolean {
        return this.status === 403 && this.code === "csrf_failed";
    }
}

type Path = keyof paths & string;

type PathMethods<P extends Path> = {
    [M in keyof paths[P]]-?: paths[P][M] extends { responses: unknown } ? M : never;
}[keyof paths[P]];

type JsonOf<T> = T extends { content: { "application/json": infer J } } ? J : unknown;

type SuccessJson<P extends Path, M extends PathMethods<P>> = paths[P][M] extends {
    responses: infer R;
}
    ? R extends Record<string | number, unknown>
        ? 200 extends keyof R
            ? JsonOf<R[200]>
            : 201 extends keyof R
              ? JsonOf<R[201]>
              : unknown
        : unknown
    : unknown;

let csrfToken: string | null = null;
let csrfInflight: Promise<string | null> | null = null;

/** Drop the cached CSRF token (e.g. after `csrf_failed`). */
export function clearCsrfToken(): void {
    csrfToken = null;
    csrfInflight = null;
}

/** Read the cached CSRF token without fetching (tests / diagnostics). */
export function peekCsrfToken(): string | null {
    return csrfToken;
}

/**
 * Map Tessera 422 `details` to a field → reason object.
 *
 * @param details - Entries from `error.details`.
 * @returns Field errors; later entries for the same field win.
 */
export function mapFieldErrors(
    details: Array<{ field: string; reason: string }> | undefined,
): FieldErrors {
    const out: FieldErrors = {};
    if (!details) {
        return out;
    }
    for (const entry of details) {
        if (entry.field) {
            out[entry.field] = entry.reason;
        }
    }
    return out;
}

/**
 * Path for login `next` from the current location (must stay under `/app`).
 *
 * @param loc - Location-like object (defaults to `window.location`).
 * @returns A `/app/...` path including search, or `/app/` when outside the SPA.
 */
export function appPathForLoginNext(
    loc: Pick<Location, "pathname" | "search"> = window.location,
): string {
    const path = `${loc.pathname}${loc.search}`;
    if (path.startsWith("/app/") || path === "/app") {
        return path === "/app" ? "/app/" : path;
    }
    return "/app/";
}

/**
 * Send the browser to the classic login UI with a safe `next` return path.
 *
 * @param next - Absolute path under `/app` or `/ui`.
 */
export function redirectToLogin(next: string = appPathForLoginNext()): void {
    const url = `/login?next=${encodeURIComponent(next)}`;
    window.location.assign(url);
}

async function fetchCsrf(): Promise<string | null> {
    if (csrfToken !== null) {
        return csrfToken;
    }
    if (csrfInflight) {
        return csrfInflight;
    }
    csrfInflight = (async () => {
        const response = await fetch("/v1/me", {
            method: "GET",
            credentials: "include",
            headers: { Accept: "application/json" },
        });
        if (response.status === 401) {
            redirectToLogin();
            throw new TesseraApiError(401, "unauthorized", "authentication required");
        }
        if (!response.ok) {
            const body = await readErrorBody(response);
            throw toApiError(response.status, body);
        }
        const data = (await response.json()) as { csrf?: string };
        csrfToken = typeof data.csrf === "string" ? data.csrf : null;
        return csrfToken;
    })();
    try {
        return await csrfInflight;
    } finally {
        csrfInflight = null;
    }
}

async function readErrorBody(response: Response): Promise<ApiErrorBody | null> {
    try {
        return (await response.json()) as ApiErrorBody;
    } catch {
        return null;
    }
}

function toApiError(status: number, body: ApiErrorBody | null): TesseraApiError {
    const code = body?.error?.code ?? "http_error";
    const message = body?.error?.message ?? `HTTP ${status}`;
    const fieldErrors = status === 422 ? mapFieldErrors(body?.error?.details) : {};
    const error = new TesseraApiError(status, code, message, fieldErrors);
    if (error.isCsrfFailed) {
        clearCsrfToken();
    }
    return error;
}

export type ApiRequestInit = Omit<RequestInit, "body" | "method"> & {
    method?: string;
    body?: BodyInit | null;
    /** Skip CSRF fetch/header (rare; bearer-only tooling). */
    skipCsrf?: boolean;
    /** Override login redirect (tests). */
    onUnauthorized?: (next: string) => void;
};

/**
 * Fetch a `/v1` path with credentials, CSRF, and Tessera error mapping.
 *
 * @param path - Absolute API path (e.g. `/v1/me`).
 * @param init - Fetch options.
 * @returns Parsed JSON when the response has a body; `undefined` for 204.
 * @throws {TesseraApiError} On non-OK responses (after 401 redirect).
 */
export async function apiRequest<T = unknown>(
    path: string,
    init: ApiRequestInit = {},
): Promise<T> {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (!headers.has("Accept")) {
        headers.set("Accept", "application/json");
    }

    const mutating = !SAFE_METHODS.has(method);
    if (mutating && !init.skipCsrf) {
        const csrf = await fetchCsrf();
        if (csrf) {
            headers.set("X-CSRF-Token", csrf);
        }
    }

    const { skipCsrf, onUnauthorized, ...rest } = init;
    void skipCsrf;
    const response = await fetch(path, {
        ...rest,
        method,
        headers,
        credentials: "include",
        body: init.body ?? null,
    });

    if (response.status === 401) {
        const next = appPathForLoginNext();
        (onUnauthorized ?? redirectToLogin)(next);
        throw new TesseraApiError(401, "unauthorized", "authentication required");
    }

    if (!response.ok) {
        const body = await readErrorBody(response);
        throw toApiError(response.status, body);
    }

    if (response.status === 204) {
        return undefined as T;
    }
    const text = await response.text();
    if (!text) {
        return undefined as T;
    }
    return JSON.parse(text) as T;
}

/**
 * Like {@link apiRequest} but returns the response body as text (YAML, plain).
 *
 * @param path - Absolute API path.
 * @param init - Fetch options.
 * @returns Response text; empty string for 204 or an empty body.
 * @throws {TesseraApiError} On non-OK responses (after 401 redirect).
 */
export async function apiRequestText(
    path: string,
    init: ApiRequestInit = {},
): Promise<string> {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (!headers.has("Accept")) {
        headers.set("Accept", "application/yaml, text/plain, */*");
    }

    const mutating = !SAFE_METHODS.has(method);
    if (mutating && !init.skipCsrf) {
        const csrf = await fetchCsrf();
        if (csrf) {
            headers.set("X-CSRF-Token", csrf);
        }
    }

    const { skipCsrf, onUnauthorized, ...rest } = init;
    void skipCsrf;
    const response = await fetch(path, {
        ...rest,
        method,
        headers,
        credentials: "include",
        body: init.body ?? null,
    });

    if (response.status === 401) {
        const next = appPathForLoginNext();
        (onUnauthorized ?? redirectToLogin)(next);
        throw new TesseraApiError(401, "unauthorized", "authentication required");
    }

    if (!response.ok) {
        const body = await readErrorBody(response);
        throw toApiError(response.status, body);
    }

    if (response.status === 204) {
        return "";
    }
    return response.text();
}

/**
 * Typed helper bound to an OpenAPI path and method.
 *
 * @param path - Path key from the generated schema.
 * @param method - HTTP method for that path.
 * @param init - Fetch options (query string must already be in `path` if needed).
 * @returns JSON body typed from the 200/201 response schema when available.
 */
export async function api<P extends Path, M extends PathMethods<P>>(
    path: P,
    method: M,
    init: ApiRequestInit = {},
): Promise<SuccessJson<P, M>> {
    return apiRequest<SuccessJson<P, M>>(path, {
        ...init,
        method: String(method).toUpperCase(),
    });
}
