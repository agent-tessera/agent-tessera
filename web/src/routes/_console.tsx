import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { meKeys } from "@/api/keys";
import { fetchMe } from "@/api/me";
import { AppShell } from "@/components/shell/AppShell";
import { AuthLoading } from "@/components/shell/AuthLoading";
import { parseEnvironmentSearch } from "@/lib/environment";

export const Route = createFileRoute("/_console")({
    validateSearch: parseEnvironmentSearch,
    component: ConsoleLayout,
});

/**
 * Authenticated console layout.
 *
 * Requires a session via `GET /v1/me`; the API client redirects 401s to login
 * with `next` preserving the current `/app` path. Child routes render through
 * `AppShell`'s `<Outlet />`.
 */
function ConsoleLayout() {
    const meQuery = useQuery({
        queryKey: meKeys.all,
        queryFn: fetchMe,
        staleTime: 60_000,
        retry: false,
    });

    if (meQuery.isPending) {
        return <AuthLoading />;
    }

    if (meQuery.isError || !meQuery.data) {
        // `fetchMe` / `apiRequest` already started the login redirect on 401.
        return <AuthLoading />;
    }

    return <AppShell me={meQuery.data} />;
}
