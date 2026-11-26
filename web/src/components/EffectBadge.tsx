import { Ban, CircleCheck, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type EffectKind = "allow" | "deny" | "require_approval";

export type EffectBadgeProps = {
    effect: EffectKind;
    className?: string;
};

const META: Record<
    EffectKind,
    {
        label: string;
        icon: typeof CircleCheck;
        className: string;
    }
> = {
    allow: {
        label: "allow",
        icon: CircleCheck,
        className: "bg-effect-allow-bg text-effect-allow-fg border-effect-allow/40",
    },
    deny: {
        label: "deny",
        icon: Ban,
        className: "bg-effect-deny-bg text-effect-deny-fg border-effect-deny/40",
    },
    require_approval: {
        label: "require approval",
        icon: ShieldAlert,
        className:
            "bg-effect-approval-bg text-effect-approval-fg border-effect-approval/40",
    },
};

/** Policy effect chip with icon + text (color is never the only signal). */
export function EffectBadge({ effect, className }: EffectBadgeProps) {
    const meta = META[effect];
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
