/** Static command-palette actions and go-to targets. */

import { requestToggleLiveTail } from "./shell-events";
import { cycleThemePreference } from "./theme";

/** A selectable palette row (search hit, recent, or static action). */
export type PaletteItem = {
    id: string;
    type: string;
    title: string;
    subtitle?: string;
    href?: string;
    /** Group heading in the list. */
    group: string;
    /** Run instead of (or after) navigation. */
    run?: () => void;
};

/** Go-to section shortcuts used by both the palette and `g` chords. */
export const GO_TO_ACTIONS: PaletteItem[] = [
    {
        id: "go-overview",
        type: "action",
        title: "Go to Overview",
        group: "Actions",
        href: "/overview",
    },
    {
        id: "go-explorer",
        type: "action",
        title: "Go to Explorer",
        group: "Actions",
        href: "/explorer",
    },
    {
        id: "go-traces",
        type: "action",
        title: "Go to Traces",
        group: "Actions",
        href: "/traces",
    },
    {
        id: "go-agents",
        type: "action",
        title: "Go to Agents",
        group: "Actions",
        href: "/agents",
    },
    {
        id: "go-approvals",
        type: "action",
        title: "Go to Approvals",
        group: "Actions",
        href: "/approvals",
    },
    {
        id: "go-insights",
        type: "action",
        title: "Go to Insights",
        group: "Actions",
        href: "/insights",
    },
    {
        id: "go-settings",
        type: "action",
        title: "Go to Settings",
        group: "Actions",
        href: "/settings",
    },
];

/** Non-navigation static actions. */
export const UTILITY_ACTIONS: PaletteItem[] = [
    {
        id: "create-agent",
        type: "action",
        title: "Create agent",
        subtitle: "Open agents with create flag",
        group: "Actions",
        href: "/agents?create=1",
    },
    {
        id: "open-approvals",
        type: "action",
        title: "Open approvals",
        group: "Actions",
        href: "/approvals",
    },
    {
        id: "toggle-live-tail",
        type: "action",
        title: "Toggle live tail",
        subtitle: "Explorer live stream",
        group: "Actions",
        href: "/explorer?live=1",
        run: () => {
            requestToggleLiveTail();
        },
    },
    {
        id: "toggle-theme",
        type: "action",
        title: "Toggle theme",
        subtitle: "Cycle light / dark / system",
        group: "Actions",
        run: () => {
            cycleThemePreference();
        },
    },
];

export const STATIC_ACTIONS: PaletteItem[] = [...GO_TO_ACTIONS, ...UTILITY_ACTIONS];

/** Map a second key after `g` to a console path. */
export const G_SHORTCUTS: Record<string, string> = {
    o: "/overview",
    e: "/explorer",
    t: "/traces",
    a: "/agents",
    p: "/approvals",
    i: "/insights",
};

/**
 * Filter static actions by a free-text query (case-insensitive substring).
 *
 * @param query - Palette input.
 */
export function filterStaticActions(query: string): PaletteItem[] {
    const needle = query.trim().toLowerCase();
    if (!needle) {
        return STATIC_ACTIONS;
    }
    return STATIC_ACTIONS.filter((item) => {
        const hay = `${item.title} ${item.subtitle ?? ""}`.toLowerCase();
        return hay.includes(needle);
    });
}

/** Human labels for search result types. */
export const TYPE_LABELS: Record<string, string> = {
    agent: "Agents",
    run: "Runs",
    approval: "Approvals",
    span: "Spans",
    resource: "Resources",
    rule: "Rules",
    recent: "Recent",
    action: "Actions",
};
