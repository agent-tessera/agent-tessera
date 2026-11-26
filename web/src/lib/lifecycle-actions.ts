/**
 * State-aware agent lifecycle actions.
 *
 * Retired agents expose no actions. The primary action is the most likely
 * next step (draft/suspended → Activate).
 */

export type LifecycleActionName = "activate" | "suspend" | "retire";

export type LifecycleAction = {
    name: LifecycleActionName;
    label: string;
    /** Primary CTA styling. */
    primary?: boolean;
    /** Destructive styling (retire / suspend). */
    destructive?: boolean;
    /** Confirm-dialog sentence; empty means confirm still opens for reason. */
    sentence: string;
};

/**
 * Return the lifecycle buttons allowed from the current status.
 *
 * @param status - Agent lifecycle status.
 * @param name - Agent name for confirm copy.
 */
export function lifecycleActionsForStatus(
    status: string,
    name: string,
): LifecycleAction[] {
    if (status === "draft") {
        return [
            {
                name: "activate",
                label: "Activate",
                primary: true,
                sentence: `Activate ${name}?`,
            },
        ];
    }
    if (status === "suspended") {
        return [
            {
                name: "activate",
                label: "Activate",
                primary: true,
                sentence: `Activate ${name}?`,
            },
            {
                name: "retire",
                label: "Retire",
                destructive: true,
                sentence: `Retire ${name}? This cannot be undone from the UI.`,
            },
        ];
    }
    if (status === "active") {
        return [
            {
                name: "suspend",
                label: "Suspend",
                sentence: `Suspend ${name}?`,
            },
            {
                name: "retire",
                label: "Retire",
                destructive: true,
                sentence: `Retire ${name}? This cannot be undone from the UI.`,
            },
        ];
    }
    return [];
}
