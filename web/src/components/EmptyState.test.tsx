import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Inbox } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
    it("renders title, body, and CTA", async () => {
        const onAction = vi.fn();
        const user = userEvent.setup();
        render(
            <EmptyState
                icon={Inbox}
                title="No agents"
                body="Create one to get started."
                actionLabel="Create agent"
                onAction={onAction}
            />,
        );
        expect(screen.getByText("No agents")).toBeInTheDocument();
        expect(screen.getByText("Create one to get started.")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Create agent" }));
        expect(onAction).toHaveBeenCalledOnce();
    });
});
