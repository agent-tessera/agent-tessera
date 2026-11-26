/**
 * Format a USD amount the way the classic admin UI does (`money` filter).
 *
 * @param value - Cost in USD.
 * @returns Display string such as `$12.50` or `<$0.01`.
 */
export function formatMoney(value: number): string {
    if (!Number.isFinite(value) || value === 0) {
        return "$0.00";
    }
    if (Math.abs(value) < 0.01) {
        return "<$0.01";
    }
    return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(value);
}
