/** Document title helpers (`<page> · Tessera`). */

const SUFFIX = "Tessera";

/**
 * Set `document.title` to `<page> · Tessera`.
 *
 * @param page - Page name shown before the product suffix.
 */
export function setPageTitle(page: string): void {
    if (typeof document === "undefined") {
        return;
    }
    const trimmed = page.trim();
    document.title = trimmed ? `${trimmed} · ${SUFFIX}` : SUFFIX;
}
