import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { clearCsrfToken } from "@/api/client";
import { server } from "@/test/msw";

import { CreateAgentDialog } from "./CreateAgentDialog";

beforeAll(() => {
    server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
    server.resetHandlers();
    clearCsrfToken();
});

afterAll(() => {
    server.close();
});

function renderDialog(props: Partial<ComponentProps<typeof CreateAgentDialog>> = {}) {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    });
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(
        <QueryClientProvider client={client}>
            <CreateAgentDialog
                open
                onClose={onClose}
                onCreated={onCreated}
                {...props}
            />
        </QueryClientProvider>,
    );
    return { onClose, onCreated };
}

describe("CreateAgentDialog", () => {
    it("maps 409 conflict onto the name field", async () => {
        const user = userEvent.setup();
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.get("/v1/owners", () =>
                HttpResponse.json({
                    items: [
                        {
                            id: "o1",
                            type: "user",
                            email: "e2e@example.com",
                            display_name: "e2e",
                            active: true,
                            created_at: "2026-11-09T00:00:00Z",
                        },
                    ],
                    next_cursor: null,
                }),
            ),
            http.post("/v1/agents", () =>
                HttpResponse.json(
                    {
                        error: {
                            code: "conflict",
                            message:
                                "an agent with this name already exists in the environment",
                        },
                    },
                    { status: 409 },
                ),
            ),
        );

        renderDialog();

        await waitFor(() =>
            expect(screen.getByTestId("create-agent-owner")).toHaveValue("o1"),
        );

        await user.type(screen.getByTestId("create-agent-name"), "palette-probe");
        await user.clear(screen.getByTestId("create-agent-environment"));
        await user.type(screen.getByTestId("create-agent-environment"), "prod");
        await user.click(screen.getByTestId("create-agent-submit"));

        expect(
            await screen.findByTestId("field-error-create-agent-name"),
        ).toHaveTextContent(
            "an agent with this name already exists in the environment",
        );
    });
});
