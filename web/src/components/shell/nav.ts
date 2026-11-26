import type { LucideIcon } from "lucide-react";
import {
    Activity,
    Bot,
    Compass,
    FileText,
    GitBranch,
    Inbox,
    LayoutDashboard,
    Settings,
} from "lucide-react";

/** Primary console navigation items. */

export type NavItem = {
    id: string;
    label: string;
    to:
        | "/overview"
        | "/explorer"
        | "/traces"
        | "/agents"
        | "/approvals"
        | "/insights"
        | "/settings";
    icon: LucideIcon;
    badge?: "approvals";
};

export const PRIMARY_NAV: NavItem[] = [
    { id: "overview", label: "Overview", to: "/overview", icon: LayoutDashboard },
    { id: "explorer", label: "Explorer", to: "/explorer", icon: Compass },
    { id: "traces", label: "Traces", to: "/traces", icon: GitBranch },
    { id: "agents", label: "Agents", to: "/agents", icon: Bot },
    {
        id: "approvals",
        label: "Approvals",
        to: "/approvals",
        icon: Inbox,
        badge: "approvals",
    },
    { id: "insights", label: "Insights", to: "/insights", icon: Activity },
];

export const BOTTOM_NAV: NavItem[] = [
    { id: "settings", label: "Settings", to: "/settings", icon: Settings },
];

/** Docs are not published yet; keep a stable in-app path for later hosting. */
export const DOCS_HREF = "/docs/";

export const DOCS_NAV = {
    id: "docs",
    label: "Docs",
    href: DOCS_HREF,
    icon: FileText,
} as const;

/** Map a pathname under `/app` to a nav label for titles and breadcrumbs. */
export function labelForPath(pathname: string): string {
    const normalized = pathname.replace(/\/$/, "") || "/";
    const match = [...PRIMARY_NAV, ...BOTTOM_NAV].find((item) => {
        return normalized === item.to || normalized.startsWith(`${item.to}/`);
    });
    return match?.label ?? "Not found";
}
