import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { fetchAgent } from "@/api/agents";
import { agentsKeys } from "@/api/keys";
import { PageHeader } from "@/components/PageHeader";
import { WelcomeConnectStep } from "@/components/welcome/WelcomeConnectStep";
import { WelcomeCreateStep } from "@/components/welcome/WelcomeCreateStep";
import { WelcomeDoneStep } from "@/components/welcome/WelcomeDoneStep";
import { WelcomePolicyStep } from "@/components/welcome/WelcomePolicyStep";
import { WelcomeStepper } from "@/components/welcome/WelcomeStepper";
import { setPageTitle } from "@/lib/page-title";
import type { WelcomeStep } from "@/lib/welcome";

export type WelcomeWizardProps = {
    step: WelcomeStep;
    agentId?: string;
    onGo: (step: WelcomeStep, agentId?: string) => void;
};

/**
 * M12 welcome stepper: create → policy template → connect → first event.
 */
export function WelcomeWizard({ step, agentId, onGo }: WelcomeWizardProps) {
    useEffect(() => {
        setPageTitle("Welcome");
    }, []);

    const agentQuery = useQuery({
        queryKey: agentsKeys.detail(agentId ?? ""),
        queryFn: () => fetchAgent(agentId!),
        enabled: Boolean(agentId) && step > 1,
    });

    const agentName = agentQuery.data?.name ?? "";
    const environment = agentQuery.data?.environment ?? "dev";

    // Guard: steps 2–4 need an agent id.
    useEffect(() => {
        if (step > 1 && !agentId) {
            onGo(1);
        }
    }, [step, agentId, onGo]);

    return (
        <div className="space-y-6" data-testid="welcome-wizard">
            <PageHeader
                title="Welcome"
                description="Create an agent, attach a policy, connect a runtime, and watch the first event."
                breadcrumbs={[
                    { label: "Overview", href: "/app/overview" },
                    { label: "Welcome" },
                ]}
            />

            <WelcomeStepper current={step} />

            <div className="border-border bg-panel rounded-md border p-4 sm:p-6">
                {step === 1 ? (
                    <WelcomeCreateStep
                        onCreated={(id) => {
                            onGo(2, id);
                        }}
                    />
                ) : null}

                {step === 2 && agentId ? (
                    agentQuery.isPending && !agentQuery.data ? (
                        <p className="text-muted-foreground text-sm">Loading agent…</p>
                    ) : (
                        <WelcomePolicyStep
                            agentId={agentId}
                            agentName={agentName || "agent"}
                            environment={environment}
                            onApplied={() => onGo(3)}
                        />
                    )
                ) : null}

                {step === 3 && agentId ? (
                    <WelcomeConnectStep agentId={agentId} onConnected={() => onGo(4)} />
                ) : null}

                {step === 4 && agentId ? (
                    <WelcomeDoneStep
                        agentId={agentId}
                        agentName={agentName || "agent"}
                    />
                ) : null}
            </div>
        </div>
    );
}
