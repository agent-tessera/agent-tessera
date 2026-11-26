import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { WebhooksSettings } from "@/components/settings/WebhooksSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/webhooks")({
    component: SettingsWebhooksPage,
});

/** `/app/settings/webhooks` — endpoints and Send test. */
function SettingsWebhooksPage() {
    useEffect(() => {
        setPageTitle("Webhooks · Settings");
    }, []);
    return <WebhooksSettings />;
}
