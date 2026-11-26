import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type EmptyStateProps = {
    icon?: LucideIcon;
    title: string;
    body?: string;
    actionLabel?: string;
    onAction?: () => void;
    action?: ReactNode;
    className?: string;
};

/** Centered empty-list placeholder with optional single CTA. */
export function EmptyState({
    icon: Icon,
    title,
    body,
    actionLabel,
    onAction,
    action,
    className,
}: EmptyStateProps) {
    return (
        <div
            className={cn(
                "text-muted-foreground flex flex-col items-center justify-center gap-3 px-6 py-12 text-center",
                className,
            )}
            role="status"
        >
            {Icon ? <Icon className="size-8 opacity-70" aria-hidden /> : null}
            <div className="space-y-1">
                <h2 className="text-foreground text-base font-semibold">{title}</h2>
                {body ? <p className="max-w-sm text-sm">{body}</p> : null}
            </div>
            {action ? (
                action
            ) : actionLabel && onAction ? (
                <Button type="button" size="sm" onClick={onAction}>
                    {actionLabel}
                </Button>
            ) : null}
        </div>
    );
}
