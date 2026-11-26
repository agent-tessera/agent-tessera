import { Outlet, createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_console/agents")({
    component: AgentsLayout,
});

/** Layout for `/agents` and `/agents/$agentId`. */
function AgentsLayout() {
    return <Outlet />;
}
