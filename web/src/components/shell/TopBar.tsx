import { Link, useRouterState } from "@tanstack/react-router";

import { labelForPath } from "@/components/shell/nav";
import { cn } from "@/lib/utils";

import { EnvironmentFilter } from "./EnvironmentFilter";
import { SearchTrigger } from "./SearchTrigger";

export type TopBarProps = {
    className?: string;
};

/** Top bar with breadcrumbs, search trigger, and environment filter. */
export function TopBar({ className }: TopBarProps) {
    const pathname = useRouterState({
        select: (state) => state.location.pathname,
    });
    const page = labelForPath(pathname);

    return (
        <header
            className={cn(
                "border-border bg-panel flex h-12 shrink-0 items-center gap-3 border-b px-4",
                className,
            )}
        >
            <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
                <ol className="text-muted-foreground flex flex-wrap items-center gap-1 text-xs">
                    <li>
                        <Link
                            to="/overview"
                            search={(prev) => prev}
                            className="hover:text-foreground underline-offset-2 hover:underline"
                        >
                            Console
                        </Link>
                    </li>
                    <li aria-hidden className="px-0.5">
                        /
                    </li>
                    <li>
                        <span className="text-foreground" aria-current="page">
                            {page}
                        </span>
                    </li>
                </ol>
            </nav>
            <SearchTrigger className="hidden sm:inline-flex" />
            <SearchTrigger compact className="sm:hidden" />
            <EnvironmentFilter />
        </header>
    );
}
