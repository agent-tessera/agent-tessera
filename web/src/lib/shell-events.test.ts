import { describe, expect, it, vi } from "vitest";

import {
    CLOSE_DRAWERS_EVENT,
    requestCloseDrawers,
    requestToggleLiveTail,
    TOGGLE_LIVE_TAIL_EVENT,
} from "./shell-events";

describe("shell events", () => {
    it("dispatches live-tail and close-drawers custom events", () => {
        const live = vi.fn();
        const close = vi.fn();
        window.addEventListener(TOGGLE_LIVE_TAIL_EVENT, live);
        window.addEventListener(CLOSE_DRAWERS_EVENT, close);
        requestToggleLiveTail();
        requestCloseDrawers();
        expect(live).toHaveBeenCalledOnce();
        expect(close).toHaveBeenCalledOnce();
        window.removeEventListener(TOGGLE_LIVE_TAIL_EVENT, live);
        window.removeEventListener(CLOSE_DRAWERS_EVENT, close);
    });
});
