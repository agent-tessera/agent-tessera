import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { SearchTrigger } from "@/components/shell/SearchTrigger";
import { setPageTitle } from "@/lib/page-title";

export const Route = createFileRoute("/_console/$")({
    component: NotFoundPage,
});

/** Unknown console paths: 404 with a search trigger. */
function NotFoundPage() {
    useEffect(() => {
        setPageTitle("Not found");
    }, []);

    return (
        <div className="space-y-6">
            <PageHeader
                title="Page not found"
                description="That console path does not exist."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Not found" },
                ]}
                actions={<SearchTrigger />}
            />
            <EmptyState
                title="Nothing here"
                body="Check the URL, use the sidebar, or open search."
                action={<SearchTrigger />}
            />
        </div>
    );
}
