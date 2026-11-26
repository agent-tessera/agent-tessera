import { Search } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/Kbd";
import { isSearchHotkey, requestOpenSearch } from "@/lib/search-events";
import { cn } from "@/lib/utils";

export type SearchTriggerProps = {
    className?: string;
    /** Compact icon-only control for tight spaces. */
    compact?: boolean;
};

/**
 * Global search trigger control.
 *
 * Dispatches `tessera:open-search` for the command palette. Does not
 * implement search itself.
 */
export function SearchTrigger({ className, compact = false }: SearchTriggerProps) {
    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (!isSearchHotkey(event)) {
                return;
            }
            event.preventDefault();
            requestOpenSearch();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    return (
        <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn(
                "text-muted-foreground justify-start gap-2 font-normal",
                compact ? "px-2" : "min-w-[12rem]",
                className,
            )}
            aria-label="Open search"
            onClick={() => requestOpenSearch()}
        >
            <Search className="size-4 shrink-0" aria-hidden />
            {compact ? null : (
                <>
                    <span className="flex-1 text-left">Search</span>
                    <span className="hidden items-center gap-1 sm:inline-flex">
                        <Kbd>/</Kbd>
                    </span>
                </>
            )}
        </Button>
    );
}
