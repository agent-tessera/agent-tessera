import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type KbdProps = {
    children: ReactNode;
    className?: string;
};

/** Keyboard shortcut glyph. */
export function Kbd({ children, className }: KbdProps) {
    return (
        <kbd
            className={cn(
                "border-border bg-muted text-muted-foreground inline-flex h-5 min-w-5 items-center justify-center rounded-md border px-1 font-mono text-[11px] font-medium",
                className,
            )}
        >
            {children}
        </kbd>
    );
}
