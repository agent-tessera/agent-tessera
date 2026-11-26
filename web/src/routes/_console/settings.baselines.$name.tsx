import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { BaselineDetail } from "@/components/settings/BaselineDetail";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/settings/baselines/$name")({
    component: SettingsBaselineDetailPage,
});

/** `/app/settings/baselines/{name}` — CodeMirror YAML, replace, delete. */
function SettingsBaselineDetailPage() {
    const { name } = Route.useParams();
    useEffect(() => {
        setPageTitle(`${name} · Baselines · Settings`);
    }, [name]);
    return <BaselineDetail name={name} />;
}
