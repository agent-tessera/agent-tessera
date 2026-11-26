/** Collapsed-sidebar persistence for the console shell. */

export const SIDEBAR_COLLAPSED_KEY = "tessera-sidebar-collapsed";

/** Read whether the sidebar is collapsed (defaults to expanded). */
export function readSidebarCollapsed(): boolean {
    if (typeof localStorage === "undefined") {
        return false;
    }
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
}

/** Persist the sidebar collapsed flag. */
export function writeSidebarCollapsed(collapsed: boolean): void {
    if (typeof localStorage === "undefined") {
        return;
    }
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
}
