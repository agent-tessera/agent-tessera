/** Cross-feature signals for the console shell (live tail, drawers). */

/** Explorer live-tail toggle (the explorer consumes this). */
export const TOGGLE_LIVE_TAIL_EVENT = "tessera:toggle-live-tail";

/** Close open drawers / panels (keyboard map documents Esc). */
export const CLOSE_DRAWERS_EVENT = "tessera:close-drawers";

/** Dispatch the live-tail toggle for the explorer. */
export function requestToggleLiveTail(): void {
    if (typeof window === "undefined") {
        return;
    }
    window.dispatchEvent(new CustomEvent(TOGGLE_LIVE_TAIL_EVENT));
}

/** Ask feature pages to close drawers and overlays. */
export function requestCloseDrawers(): void {
    if (typeof window === "undefined") {
        return;
    }
    window.dispatchEvent(new CustomEvent(CLOSE_DRAWERS_EVENT));
}
