import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
    it("exposes the message as an alert and retries", async () => {
        const onRetry = vi.fn();
        const user = userEvent.setup();
        render(<ErrorState message="Network down" onRetry={onRetry} />);
        expect(screen.getByRole("alert")).toHaveTextContent("Network down");
        await user.click(screen.getByRole("button", { name: "Retry" }));
        expect(onRetry).toHaveBeenCalledOnce();
    });
});
