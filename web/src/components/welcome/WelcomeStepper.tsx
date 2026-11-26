import { Check } from "lucide-react";

import { WELCOME_STEPS, type WelcomeStep } from "@/lib/welcome";
import { cn } from "@/lib/utils";

export type WelcomeStepperProps = {
    current: WelcomeStep;
};

/** Horizontal 4-step indicator for the welcome wizard. */
export function WelcomeStepper({ current }: WelcomeStepperProps) {
    return (
        <ol
            className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-0"
            data-testid="welcome-stepper"
            aria-label="Onboarding steps"
        >
            {WELCOME_STEPS.map((item, index) => {
                const done = item.step < current;
                const active = item.step === current;
                return (
                    <li
                        key={item.step}
                        className={cn(
                            "flex items-center gap-2 sm:flex-1",
                            index < WELCOME_STEPS.length - 1 && "sm:pr-2",
                        )}
                        data-testid={`welcome-step-indicator-${item.step}`}
                        data-state={done ? "done" : active ? "current" : "todo"}
                    >
                        <span
                            className={cn(
                                "flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold",
                                done && "bg-primary text-primary-foreground",
                                active &&
                                    !done &&
                                    "bg-accent-soft text-primary ring-primary/30 ring-2",
                                !done && !active && "bg-muted text-muted-foreground",
                            )}
                            aria-hidden
                        >
                            {done ? <Check className="size-3.5" /> : item.step}
                        </span>
                        <span
                            className={cn(
                                "text-sm",
                                active
                                    ? "text-foreground font-medium"
                                    : "text-muted-foreground",
                            )}
                        >
                            {item.label}
                        </span>
                        {index < WELCOME_STEPS.length - 1 ? (
                            <span
                                className="bg-border mx-2 hidden h-px flex-1 sm:block"
                                aria-hidden
                            />
                        ) : null}
                    </li>
                );
            })}
        </ol>
    );
}
