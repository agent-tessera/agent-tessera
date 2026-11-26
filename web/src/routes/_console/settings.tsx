import { Outlet, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

import { PageHeader } from "@/components/PageHeader";
import { SettingsNav } from "@/components/settings/SettingsNav";
import { setPageTitle } from "@/lib/page-title";

export type SettingsSearch = {
    environment?: string;
    /** Legacy Overview deep-link; redirected to `/settings/audit`. */
    section?: string;
};

function parseSettingsSearch(search: Record<string, unknown>): SettingsSearch {
    const out: SettingsSearch = {};
    const environment = search.environment;
    if (typeof environment === "string" && environment.trim()) {
        out.environment = environment.trim();
    }
    const section = search.section;
    if (typeof section === "string" && section.trim()) {
        out.section = section.trim();
    }
    return out;
}

export const Route = createFileRoute("/_console/settings")({
    validateSearch: parseSettingsSearch,
    component: SettingsLayout,
});

/**
 * Settings shell with section sub-nav (owners, alerts, webhooks, baselines,
 * custody, admin keys, audit).
 */
function SettingsLayout() {
    const search = Route.useSearch();
    const navigate = useNavigate();

    useEffect(() => {
        setPageTitle("Settings");
    }, []);

    useEffect(() => {
        if (search.section !== "audit") {
            return;
        }
        void navigate({
            to: "/settings/audit",
            search: (prev) => {
                const next = { ...prev } as SettingsSearch;
                delete next.section;
                return next;
            },
            replace: true,
        });
    }, [search.section, navigate]);

    return (
        <div className="space-y-6" data-testid="settings-page">
            <PageHeader
                title="Settings"
                description="Owners, alerts, webhooks, baselines, custody, admin keys, and audit."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Settings" },
                ]}
            />
            <SettingsNav />
            <Outlet />
        </div>
    );
}
