import { beforeEach, describe, expect, it } from "vitest";

import {
    applyTheme,
    cycleThemePreference,
    readThemePreference,
    THEME_KEY,
} from "./theme";

describe("cycleThemePreference", () => {
    beforeEach(() => {
        localStorage.removeItem(THEME_KEY);
        document.documentElement.classList.remove("dark");
    });

    it("cycles light → dark → system", () => {
        applyTheme("light");
        expect(cycleThemePreference()).toBe("dark");
        expect(readThemePreference()).toBe("dark");
        expect(cycleThemePreference()).toBe("system");
        expect(cycleThemePreference()).toBe("light");
    });
});
