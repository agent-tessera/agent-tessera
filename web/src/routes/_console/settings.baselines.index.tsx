import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { BaselinesSettings } from "@/components/settings/BaselinesSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/baselines/")({
    component: SettingsBaselinesIndexPage,
});

/** `/app/settings/baselines` — list and upload. */
function SettingsBaselinesIndexPage() {
    useEffect(() => {
        setPageTitle("Baselines · Settings");
    }, []);
    return <BaselinesSettings />;
}
