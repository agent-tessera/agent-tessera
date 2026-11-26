import { Outlet, createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_console/settings/baselines")({
    component: SettingsBaselinesLayout,
});

/** Layout for baselines list and `$name` detail. */
function SettingsBaselinesLayout() {
    return <Outlet />;
}
