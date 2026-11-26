/** Theme preference helpers for the console. */

export type ThemePreference = "light" | "dark" | "system";

export const THEME_KEY = "tessera-theme";

/** Read the stored theme preference (defaults to system). */
export function readThemePreference(): ThemePreference {
    if (typeof localStorage === "undefined") {
        return "system";
    }
    const value = localStorage.getItem(THEME_KEY);
    if (value === "light" || value === "dark" || value === "system") {
        return value;
    }
    return "system";
}

/** Whether the resolved theme is dark for a preference. */
export function resolvesDark(preference: ThemePreference): boolean {
    if (preference === "dark") {
        return true;
    }
    if (preference === "light") {
        return false;
    }
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
        return false;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Persist and apply a theme preference on `<html>`. */
export function applyTheme(preference: ThemePreference): void {
    if (typeof document === "undefined") {
        return;
    }
    localStorage.setItem(THEME_KEY, preference);
    document.documentElement.classList.toggle("dark", resolvesDark(preference));
}

/** Apply the stored preference (or system default) on boot. */
export function initTheme(): ThemePreference {
    const preference = readThemePreference();
    applyTheme(preference);
    return preference;
}

const THEME_CYCLE: ThemePreference[] = ["light", "dark", "system"];

/**
 * Advance the theme preference light → dark → system → light.
 *
 * @returns The preference that was applied.
 */
export function cycleThemePreference(): ThemePreference {
    const current = readThemePreference();
    const index = THEME_CYCLE.indexOf(current);
    const next = THEME_CYCLE[(index + 1) % THEME_CYCLE.length] ?? "system";
    applyTheme(next);
    return next;
}
