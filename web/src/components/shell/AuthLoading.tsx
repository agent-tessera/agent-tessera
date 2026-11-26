/** Minimal loading chrome while `GET /v1/me` resolves. */
export function AuthLoading() {
    return (
        <div
            className="bg-background text-muted-foreground flex min-h-screen items-center justify-center text-sm"
            role="status"
            aria-live="polite"
        >
            Loading console…
        </div>
    );
}
