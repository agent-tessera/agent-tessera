import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { AlertsSettings } from "@/components/settings/AlertsSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/alerts")({
    component: SettingsAlertsPage,
});

/** `/app/settings/alerts` — threshold rules. */
function SettingsAlertsPage() {
    useEffect(() => {
        setPageTitle("Alerts · Settings");
    }, []);
    return <AlertsSettings />;
}
