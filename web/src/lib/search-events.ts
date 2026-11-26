/** Hooks for the global search trigger (the command palette consumes these). */

/** Custom event name the shell dispatches when search should open. */
export const OPEN_SEARCH_EVENT = "tessera:open-search";

/** Dispatch the console search-open signal for the command palette to handle. */
export function requestOpenSearch(): void {
    if (typeof window === "undefined") {
        return;
    }
    window.dispatchEvent(new CustomEvent(OPEN_SEARCH_EVENT));
}

/**
 * Whether a keyboard event should open search (⌘K / Ctrl-K / `/`).
 *
 * Ignores `/` when focus is already in an editable field.
 *
 * @param event - Keyboard event from `window` or a control.
 */
export function isSearchHotkey(event: KeyboardEvent): boolean {
    const key = event.key;
    if ((event.metaKey || event.ctrlKey) && key.toLowerCase() === "k") {
        return true;
    }
    if (key !== "/") {
        return false;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) {
        return false;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
        return true;
    }
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return false;
    }
    return !target.isContentEditable;
}
