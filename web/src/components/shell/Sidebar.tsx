import { Link, useRouterState } from "@tanstack/react-router";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import type { MeResponse } from "@/api/me";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { ActorMenu } from "./ActorMenu";
import { ApprovalsBadge } from "./ApprovalsBadge";
import { BOTTOM_NAV, DOCS_NAV, PRIMARY_NAV } from "./nav";

export type SidebarProps = {
    me: MeResponse;
    collapsed: boolean;
    onCollapsedChange: (collapsed: boolean) => void;
};

/** Left navigation: primary sections, settings, docs, and actor menu. */
export function Sidebar({ me, collapsed, onCollapsedChange }: SidebarProps) {
    const pathname = useRouterState({
        select: (state) => state.location.pathname,
    });

    return (
        <aside
            className={cn(
                "border-border bg-panel sticky top-0 flex h-screen shrink-0 flex-col border-r transition-[width] duration-150",
                collapsed ? "w-14" : "w-56",
            )}
            aria-label="Console"
        >
            <div
                className={cn(
                    "border-border flex h-12 items-center border-b px-3",
                    collapsed ? "justify-center" : "justify-between gap-2",
                )}
            >
                {collapsed ? null : (
                    <Link
                        to="/overview"
                        search={(prev) => prev}
                        className="text-foreground truncate text-sm font-semibold tracking-tight"
                    >
                        Tessera
                    </Link>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="px-2"
                    aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                    aria-pressed={collapsed}
                    onClick={() => onCollapsedChange(!collapsed)}
                >
                    {collapsed ? (
                        <PanelLeftOpen className="size-4" aria-hidden />
                    ) : (
                        <PanelLeftClose className="size-4" aria-hidden />
                    )}
                </Button>
            </div>

            <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="Primary">
                {PRIMARY_NAV.map((item) => {
                    const active =
                        pathname === item.to || pathname.startsWith(`${item.to}/`);
                    const Icon = item.icon;
                    return (
                        <Link
                            key={item.id}
                            to={item.to}
                            search={(prev) => prev}
                            aria-current={active ? "page" : undefined}
                            title={item.label}
                            className={cn(
                                "hover:bg-muted relative flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                                active
                                    ? "bg-accent-soft text-foreground font-medium"
                                    : "text-muted-foreground",
                                collapsed && "justify-center px-0",
                            )}
                        >
                            <Icon className="size-4 shrink-0" aria-hidden />
                            {collapsed ? (
                                <span className="sr-only">{item.label}</span>
                            ) : (
                                <span className="flex-1 truncate">{item.label}</span>
                            )}
                            {item.badge === "approvals" ? (
                                <ApprovalsBadge
                                    className={
                                        collapsed
                                            ? "absolute top-0.5 right-0.5 scale-90"
                                            : undefined
                                    }
                                />
                            ) : null}
                        </Link>
                    );
                })}
            </nav>

            <div className="border-border mt-auto flex flex-col gap-1 border-t p-2">
                {BOTTOM_NAV.map((item) => {
                    const active =
                        pathname === item.to || pathname.startsWith(`${item.to}/`);
                    const Icon = item.icon;
                    return (
                        <Link
                            key={item.id}
                            to={item.to}
                            search={(prev) => prev}
                            aria-current={active ? "page" : undefined}
                            title={item.label}
                            className={cn(
                                "hover:bg-muted flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                                active
                                    ? "bg-accent-soft text-foreground font-medium"
                                    : "text-muted-foreground",
                                collapsed && "justify-center px-0",
                            )}
                        >
                            <Icon className="size-4 shrink-0" aria-hidden />
                            {collapsed ? (
                                <span className="sr-only">{item.label}</span>
                            ) : (
                                <span className="truncate">{item.label}</span>
                            )}
                        </Link>
                    );
                })}
                <a
                    href={DOCS_NAV.href}
                    title={DOCS_NAV.label}
                    className={cn(
                        "text-muted-foreground hover:bg-muted flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                        collapsed && "justify-center px-0",
                    )}
                >
                    <DOCS_NAV.icon className="size-4 shrink-0" aria-hidden />
                    {collapsed ? (
                        <span className="sr-only">{DOCS_NAV.label}</span>
                    ) : (
                        <span>{DOCS_NAV.label}</span>
                    )}
                </a>
                <ActorMenu
                    me={me}
                    compact={collapsed}
                    className={collapsed ? "flex justify-center" : undefined}
                />
            </div>
        </aside>
    );
}
