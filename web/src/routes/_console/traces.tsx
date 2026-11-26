import { Outlet, createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_console/traces")({
    component: TracesLayout,
});

/** Layout for `/traces` and `/traces/$runId`. */
function TracesLayout() {
    return <Outlet />;
}
