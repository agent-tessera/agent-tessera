import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { OwnersSettings } from "@/components/settings/OwnersSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/owners")({
    component: SettingsOwnersPage,
});

/** `/app/settings/owners` — directory of users and teams. */
function SettingsOwnersPage() {
    useEffect(() => {
        setPageTitle("Owners · Settings");
    }, []);
    return <OwnersSettings />;
}
