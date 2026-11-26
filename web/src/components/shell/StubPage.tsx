import { useEffect } from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { setPageTitle } from "@/lib/page-title";

export type StubPageProps = {
    title: string;
    description?: string;
};

/** Placeholder page for routes whose feature work lands in later tasks. */
export function StubPage({ title, description }: StubPageProps) {
    useEffect(() => {
        setPageTitle(title);
    }, [title]);

    return (
        <div className="space-y-6">
            <PageHeader
                title={title}
                description={description}
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: title },
                ]}
            />
            <EmptyState
                title="Coming in a later task"
                body="This surface is wired into the shell; feature content arrives soon."
            />
        </div>
    );
}
