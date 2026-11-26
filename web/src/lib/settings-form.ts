import { cn } from "@/lib/utils";

/**
 * Shared input chrome; marks invalid fields for a11y.
 *
 * @param error - Optional validation message.
 */
export function settingsInputClass(error?: string): string {
    return cn(
        "border-border bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none",
        error && "border-status-error",
    );
}
