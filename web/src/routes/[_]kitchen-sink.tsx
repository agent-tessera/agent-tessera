import { createFileRoute, notFound } from "@tanstack/react-router";
import { createColumnHelper } from "@tanstack/react-table";
import { Inbox } from "lucide-react";
import { useMemo } from "react";

import { DataTable } from "@/components/DataTable";
import { EffectBadge } from "@/components/EffectBadge";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Kbd } from "@/components/Kbd";
import { Mono } from "@/components/Mono";
import { PageHeader } from "@/components/PageHeader";
import { RelativeTime } from "@/components/RelativeTime";
import { StatusBadge } from "@/components/StatusBadge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/_kitchen-sink")({
    beforeLoad: () => {
        if (!import.meta.env.DEV) {
            throw notFound();
        }
    },
    component: KitchenSinkPage,
});

type DemoRow = { id: number; name: string; effect: "allow" | "deny" };

const columnHelper = createColumnHelper<DemoRow>();

const columns = [
    columnHelper.accessor("id", { header: "ID", size: 72 }),
    columnHelper.accessor("name", {
        header: "Name",
        size: 180,
        cell: (info) => <Mono>{info.getValue()}</Mono>,
    }),
    columnHelper.accessor("effect", {
        header: "Effect",
        size: 140,
        cell: (info) => <EffectBadge effect={info.getValue()} />,
    }),
];

/** Dev-only showcase of design-system primitives. */
function KitchenSinkPage() {
    const rows = useMemo(
        () =>
            Array.from({ length: 200 }, (_, id) => ({
                id,
                name: `agent-${id.toString(16)}`,
                effect: id % 3 === 0 ? ("deny" as const) : ("allow" as const),
            })),
        [],
    );

    return (
        <main className="mx-auto flex max-w-5xl flex-col gap-8 p-8">
            <PageHeader
                title="Kitchen sink"
                description="Dev-only design system showcase."
                breadcrumbs={[
                    { label: "Console", href: "/app/" },
                    { label: "Kitchen sink" },
                ]}
                actions={<ThemeToggle />}
            />

            <section className="space-y-3">
                <h2 className="text-sm font-semibold">Badges</h2>
                <div className="flex flex-wrap gap-2">
                    <EffectBadge effect="allow" />
                    <EffectBadge effect="deny" />
                    <EffectBadge effect="require_approval" />
                    <StatusBadge status="ok" />
                    <StatusBadge status="running" />
                    <StatusBadge status="error" />
                    <StatusBadge status="revoked" />
                </div>
            </section>

            <section className="space-y-3">
                <h2 className="text-sm font-semibold">Typography & chrome</h2>
                <div className="flex flex-wrap items-center gap-3 text-sm">
                    <Mono>deadbeefcafebabe</Mono>
                    <RelativeTime value={new Date(Date.now() - 90_000)} />
                    <span>
                        Search <Kbd>⌘</Kbd>
                        <Kbd>K</Kbd>
                    </span>
                    <Button type="button" size="sm" variant="outline">
                        Outline
                    </Button>
                </div>
            </section>

            <section className="space-y-3">
                <h2 className="text-sm font-semibold">States</h2>
                <div className="grid gap-4 md:grid-cols-2">
                    <EmptyState
                        icon={Inbox}
                        title="No rows"
                        body="Filters matched nothing."
                        actionLabel="Clear filters"
                        onAction={() => undefined}
                    />
                    <ErrorState
                        message="Upstream timed out."
                        onRetry={() => undefined}
                    />
                </div>
            </section>

            <section className="space-y-3">
                <h2 className="text-sm font-semibold">DataTable</h2>
                <DataTable
                    data={rows}
                    columns={columns}
                    height={280}
                    aria-label="Demo rows"
                />
            </section>
        </main>
    );
}
