import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_console/onboarding")({
    beforeLoad: ({ search }) => {
        throw redirect({
            to: "/welcome",
            search,
        });
    },
});
