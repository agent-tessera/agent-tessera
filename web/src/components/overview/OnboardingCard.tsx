import { Link } from "@tanstack/react-router";
import { Rocket } from "lucide-react";

import { Button } from "@/components/ui/button";

/** Full-width first-run card linking to the welcome stepper (`/app/welcome`). */
export function OnboardingCard() {
    return (
        <section
            data-testid="onboarding-card"
            className="bg-panel border-border flex w-full flex-col gap-4 rounded-md border p-6 sm:flex-row sm:items-center sm:justify-between"
        >
            <div className="flex min-w-0 items-start gap-3">
                <div className="bg-accent-soft text-primary flex size-10 shrink-0 items-center justify-center rounded-md">
                    <Rocket className="size-5" aria-hidden />
                </div>
                <div className="space-y-1">
                    <h2 className="text-foreground text-base font-semibold">
                        Welcome to Tessera
                    </h2>
                    <p className="text-muted-foreground max-w-xl text-sm">
                        Create your first agent, attach a policy, and connect a runtime
                        to start governing decisions.
                    </p>
                </div>
            </div>
            <Button asChild size="sm" className="shrink-0">
                <Link to="/welcome" data-testid="onboarding-start">
                    Start onboarding
                </Link>
            </Button>
        </section>
    );
}
