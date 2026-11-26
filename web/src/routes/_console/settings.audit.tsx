import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { AuditSettings } from "@/components/settings/AuditSettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/audit")({
    component: SettingsAuditPage,
});

/** `/app/settings/audit` — verify status and hash-chained log. */
function SettingsAuditPage() {
    useEffect(() => {
        setPageTitle("Audit · Settings");
    }, []);
    return <AuditSettings />;
}
