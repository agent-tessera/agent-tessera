import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Drawer } from "./Drawer";

describe("Drawer", () => {
    it("renders children when open and closes from the button", async () => {
        const user = userEvent.setup();
        const onClose = vi.fn();
        render(
            <Drawer open title="Details" onClose={onClose}>
                <p>Body copy</p>
            </Drawer>,
        );
        expect(screen.getByTestId("drawer")).toBeInTheDocument();
        expect(screen.getByText("Body copy")).toBeInTheDocument();
        await user.click(screen.getByTestId("drawer-close"));
        expect(onClose).toHaveBeenCalled();
    });

    it("renders nothing when closed", () => {
        render(
            <Drawer open={false} title="Details" onClose={() => undefined}>
                hidden
            </Drawer>,
        );
        expect(screen.queryByTestId("drawer")).not.toBeInTheDocument();
    });
});
