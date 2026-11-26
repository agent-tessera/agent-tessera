import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { parseSearch, stringifySearch } from "./lib/search-params";
import { initTheme } from "./lib/theme";
import { routeTree } from "./routeTree.gen";
import "./styles/globals.css";

initTheme();

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            staleTime: 30_000,
            retry: false,
        },
    },
});

const router = createRouter({
    routeTree,
    basepath: "/app",
    context: { queryClient },
    stringifySearch,
    parseSearch,
});

declare module "@tanstack/react-router" {
    interface Register {
        router: typeof router;
    }
}

const root = document.getElementById("root");
if (!root) {
    throw new Error("root element missing");
}

createRoot(root).render(
    <StrictMode>
        <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
        </QueryClientProvider>
    </StrictMode>,
);
