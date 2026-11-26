import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RelativeTime } from "./RelativeTime";

describe("RelativeTime", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-09T20:00:00.000Z"));
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("shows a relative label and UTC tooltip", () => {
        render(<RelativeTime value="2026-11-09T19:00:00.000Z" />);
        const el = screen.getByRole("time");
        expect(el).toHaveAttribute("title", "2026-11-09T19:00:00Z");
        expect(el).toHaveAttribute("dateTime", "2026-11-09T19:00:00.000Z");
        expect(el.textContent?.length).toBeGreaterThan(0);
    });
});
