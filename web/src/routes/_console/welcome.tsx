import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { WelcomeWizard } from "@/components/welcome/WelcomeWizard";
import { parseEnvironmentSearch } from "@/lib/environment";
import { parseWelcomeStep, type WelcomeStep } from "@/lib/welcome";

export type WelcomeSearch = {
    step?: WelcomeStep;
    agent?: string;
    environment?: string;
};

function parseWelcomeSearch(search: Record<string, unknown>): WelcomeSearch {
    const base = parseEnvironmentSearch(search);
    const out: WelcomeSearch = { ...base };
    const step = parseWelcomeStep(search.step);
    if (step !== 1) {
        out.step = step;
    }
    if (typeof search.agent === "string" && search.agent.trim()) {
        out.agent = search.agent.trim();
    }
    return out;
}

export const Route = createFileRoute("/_console/welcome")({
    validateSearch: parseWelcomeSearch,
    component: WelcomePage,
});

/** M12 onboarding welcome stepper at `/app/welcome`. */
function WelcomePage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const step = search.step ?? 1;

    const onGo = useCallback(
        (next: WelcomeStep, nextAgentId?: string) => {
            void navigate({
                search: (prev) => ({
                    ...prev,
                    step: next === 1 ? undefined : next,
                    agent: nextAgentId ?? prev.agent,
                }),
                replace: true,
            });
        },
        [navigate],
    );

    return <WelcomeWizard step={step} agentId={search.agent} onGo={onGo} />;
}
