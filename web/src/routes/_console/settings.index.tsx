import { Link, createFileRoute } from "@tanstack/react-router";

import { SETTINGS_SECTIONS } from "@/lib/settings-nav";

export const Route = createFileRoute("/_console/settings/")({
    component: SettingsIndexPage,
});

/** Settings index: links to each M12 section. */
function SettingsIndexPage() {
    return (
        <ul className="grid gap-3 sm:grid-cols-2" data-testid="settings-index">
            {SETTINGS_SECTIONS.map((section) => (
                <li key={section.id}>
                    <Link
                        to={section.to}
                        search={(prev) => prev}
                        className="border-border hover:bg-muted/40 block rounded-md border p-4"
                        data-testid={`settings-index-${section.id}`}
                    >
                        <span className="text-foreground text-sm font-medium">
                            {section.label}
                        </span>
                        <p className="text-muted-foreground mt-1 text-sm">
                            {section.description}
                        </p>
                    </Link>
                </li>
            ))}
        </ul>
    );
}
