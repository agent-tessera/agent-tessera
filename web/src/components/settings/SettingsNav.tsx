import { Link, useRouterState } from "@tanstack/react-router";

import { SETTINGS_SECTIONS } from "@/lib/settings-nav";
import { cn } from "@/lib/utils";

/** Horizontal sub-nav for settings sections. */
export function SettingsNav() {
    const pathname = useRouterState({
        select: (state) => state.location.pathname,
    });

    return (
        <nav
            aria-label="Settings sections"
            className="border-border flex flex-wrap gap-1 border-b pb-3"
            data-testid="settings-nav"
        >
            {SETTINGS_SECTIONS.map((section) => {
                const active =
                    pathname === section.to || pathname.startsWith(`${section.to}/`);
                return (
                    <Link
                        key={section.id}
                        to={section.to}
                        search={(prev) => prev}
                        aria-current={active ? "page" : undefined}
                        data-testid={`settings-nav-${section.id}`}
                        className={cn(
                            "rounded-md px-2.5 py-1.5 text-sm",
                            active
                                ? "bg-accent-soft text-foreground font-medium"
                                : "text-muted-foreground hover:bg-muted",
                        )}
                    >
                        {section.label}
                    </Link>
                );
            })}
        </nav>
    );
}
