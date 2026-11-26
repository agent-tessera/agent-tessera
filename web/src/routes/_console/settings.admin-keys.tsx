import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { AdminKeysSettings } from "@/components/settings/AdminKeysSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/admin-keys")({
    component: SettingsAdminKeysPage,
});

/** `/app/settings/admin-keys` — list, create once, revoke. */
function SettingsAdminKeysPage() {
    useEffect(() => {
        setPageTitle("Admin keys · Settings");
    }, []);
    return <AdminKeysSettings />;
}
