import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "./ThemeToggle";
import { THEME_KEY } from "@/lib/theme";

describe("ThemeToggle", () => {
    beforeEach(() => {
        localStorage.clear();
        document.documentElement.classList.remove("dark");
        vi.stubGlobal(
            "matchMedia",
            vi.fn().mockImplementation((query: string) => ({
                matches: false,
                media: query,
                addEventListener: vi.fn(),
                removeEventListener: vi.fn(),
                addListener: vi.fn(),
                removeListener: vi.fn(),
                dispatchEvent: vi.fn(),
            })),
        );
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("persists the selected theme to localStorage and toggles .dark", async () => {
        const user = userEvent.setup();
        render(<ThemeToggle />);

        await user.click(screen.getByRole("button", { name: "Dark" }));
        expect(localStorage.getItem(THEME_KEY)).toBe("dark");
        expect(document.documentElement.classList.contains("dark")).toBe(true);

        await user.click(screen.getByRole("button", { name: "Light" }));
        expect(localStorage.getItem(THEME_KEY)).toBe("light");
        expect(document.documentElement.classList.contains("dark")).toBe(false);

        await user.click(screen.getByRole("button", { name: "System" }));
        expect(localStorage.getItem(THEME_KEY)).toBe("system");
    });
});
