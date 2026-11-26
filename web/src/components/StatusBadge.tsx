import { CircleAlert, CircleCheck, CircleDot, CircleOff } from "lucide-react";

import { cn } from "@/lib/utils";

export type StatusKind = "ok" | "error" | "revoked" | "running";

export type StatusBadgeProps = {
    status: StatusKind;
    className?: string;
};

const META: Record<
    StatusKind,
    {
        label: string;
        icon: typeof CircleCheck;
        className: string;
    }
> = {
    ok: {
        label: "ok",
        icon: CircleCheck,
        className: "bg-status-ok-bg text-status-ok-fg border-status-ok/40",
    },
    error: {
        label: "error",
        icon: CircleAlert,
        className: "border-status-error bg-status-error-bg text-status-error-fg border",
    },
    revoked: {
        label: "revoked",
        icon: CircleOff,
        className:
            "bg-status-revoked-bg text-status-revoked-fg border-status-revoked/40",
    },
    running: {
        label: "running",
        icon: CircleDot,
        className:
            "bg-status-running-bg text-status-running-fg border-status-running/40",
    },
};

/** Run/agent status chip with icon + text. */
export function StatusBadge({ status, className }: StatusBadgeProps) {
    const meta = META[status];
    const Icon = meta.icon;
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium",
                meta.className,
                className,
            )}
        >
            <Icon className="size-3.5 shrink-0" aria-hidden />
            <span>{meta.label}</span>
        </span>
    );
}
