import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { CustodySettings } from "@/components/settings/CustodySettings";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/custody")({
    component: SettingsCustodyPage,
});

/** `/app/settings/custody` — encrypted provider keys. */
function SettingsCustodyPage() {
    useEffect(() => {
        setPageTitle("Custody · Settings");
    }, []);
    return <CustodySettings />;
}
