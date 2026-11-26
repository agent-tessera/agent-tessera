import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_console/")({
    beforeLoad: ({ search }) => {
        throw redirect({
            to: "/overview",
            search,
        });
    },
});
