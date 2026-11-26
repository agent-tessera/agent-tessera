import { Fragment, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Breadcrumb = {
    label: string;
    href?: string;
};

export type PageHeaderProps = {
    title: string;
    description?: string;
    breadcrumbs?: Breadcrumb[];
    actions?: ReactNode;
    className?: string;
};

/** Page title row with optional breadcrumbs and action slot. */
export function PageHeader({
    title,
    description,
    breadcrumbs,
    actions,
    className,
}: PageHeaderProps) {
    return (
        <header
            className={cn(
                "border-border flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between",
                className,
            )}
        >
            <div className="min-w-0 space-y-1">
                {breadcrumbs && breadcrumbs.length > 0 ? (
                    <nav
                        aria-label="Breadcrumb"
                        className="text-muted-foreground text-xs"
                    >
                        <ol className="flex flex-wrap items-center gap-1">
                            {breadcrumbs.map((crumb, index) => (
                                <Fragment key={`${crumb.label}-${index}`}>
                                    {index > 0 ? (
                                        <li aria-hidden className="px-0.5">
                                            /
                                        </li>
                                    ) : null}
                                    <li>
                                        {crumb.href ? (
                                            <a
                                                href={crumb.href}
                                                className="hover:text-foreground underline-offset-2 hover:underline"
                                            >
                                                {crumb.label}
                                            </a>
                                        ) : (
                                            <span className="text-foreground">
                                                {crumb.label}
                                            </span>
                                        )}
                                    </li>
                                </Fragment>
                            ))}
                        </ol>
                    </nav>
                ) : null}
                <h1 className="text-foreground truncate text-xl font-semibold tracking-tight">
                    {title}
                </h1>
                {description ? (
                    <p className="text-muted-foreground text-sm">{description}</p>
                ) : null}
            </div>
            {actions ? (
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {actions}
                </div>
            ) : null}
        </header>
    );
}
