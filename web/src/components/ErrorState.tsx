import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ErrorStateProps = {
    title?: string;
    message: string;
    onRetry?: () => void;
    retryLabel?: string;
    action?: ReactNode;
    className?: string;
};

/** Inline error panel with optional retry. */
export function ErrorState({
    title = "Something went wrong",
    message,
    onRetry,
    retryLabel = "Retry",
    action,
    className,
}: ErrorStateProps) {
    return (
        <div
            className={cn(
                "border-status-error/50 bg-panel flex flex-col items-start gap-3 rounded-md border p-4",
                className,
            )}
            role="alert"
        >
            <div className="flex items-start gap-2">
                <CircleAlert
                    className="text-status-error mt-0.5 size-5 shrink-0"
                    aria-hidden
                />
                <div className="space-y-1">
                    <h2 className="text-foreground text-sm font-semibold">{title}</h2>
                    <p className="text-muted-foreground text-sm">{message}</p>
                </div>
            </div>
            {action ? (
                action
            ) : onRetry ? (
                <Button type="button" size="sm" variant="outline" onClick={onRetry}>
                    {retryLabel}
                </Button>
            ) : null}
        </div>
    );
}
