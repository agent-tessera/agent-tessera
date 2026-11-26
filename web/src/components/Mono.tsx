import { useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type MonoProps = {
    children: ReactNode;
    /** Value copied on click; defaults to string children. */
    copyValue?: string;
    className?: string;
};

async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** Monospace id/resource text; click copies when a string value is available. */
export function Mono({ children, copyValue, className }: MonoProps) {
    const [copied, setCopied] = useState(false);
    const text =
        copyValue ??
        (typeof children === "string" || typeof children === "number"
            ? String(children)
            : undefined);

    const onClick = () => {
        if (!text) {
            return;
        }
        void copyText(text).then((ok) => {
            if (!ok) {
                return;
            }
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
        });
    };

    if (!text) {
        return (
            <code className={cn("font-mono text-[13px]", className)}>{children}</code>
        );
    }

    return (
        <button
            type="button"
            onClick={onClick}
            title={copied ? "Copied" : "Copy"}
            aria-label={copied ? "Copied" : `Copy ${text}`}
            className={cn(
                "focus-visible:ring-ring rounded-sm font-mono text-[13px] hover:underline focus-visible:ring-2 focus-visible:outline-none",
                className,
            )}
        >
            {children}
        </button>
    );
}
