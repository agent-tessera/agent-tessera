import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { fetchAgentInstances } from "@/api/agents";
import { agentsKeys } from "@/api/keys";
import { AgentConnect } from "@/components/agents/AgentConnect";
import { Button } from "@/components/ui/button";
import { firstConnectedShortId } from "@/lib/agent-connect";

export type WelcomeConnectStepProps = {
    agentId: string;
    onConnected: () => void;
};

const POLL_MS = 2000;

/**
 * Step 3: reuse the Connect tab (token + poll) and continue once live.
 */
export function WelcomeConnectStep({ agentId, onConnected }: WelcomeConnectStepProps) {
    const instancesQuery = useQuery({
        queryKey: agentsKeys.instances(agentId),
        queryFn: () => fetchAgentInstances(agentId, { limit: 100 }),
        refetchInterval: (query) => {
            const short = firstConnectedShortId(query.state.data?.items ?? []);
            return short ? false : POLL_MS;
        },
    });

    const connected = useMemo(
        () => Boolean(firstConnectedShortId(instancesQuery.data?.items ?? [])),
        [instancesQuery.data],
    );

    return (
        <div className="space-y-6" data-testid="welcome-connect-step">
            <div className="space-y-1">
                <h2 className="text-sm font-semibold">Connect an instance</h2>
                <p className="text-muted-foreground text-sm">
                    Mint a registration token for production runtimes, or in development
                    mode register with{" "}
                    <code className="font-mono text-[13px]">grant_type=dev</code>. This
                    step waits for the first live connection.
                </p>
            </div>

            <AgentConnect agentId={agentId} />

            <Button
                type="button"
                size="sm"
                data-testid="welcome-connect-continue"
                disabled={!connected}
                onClick={onConnected}
            >
                {connected ? "Continue" : "Waiting for connection…"}
            </Button>
        </div>
    );
}
