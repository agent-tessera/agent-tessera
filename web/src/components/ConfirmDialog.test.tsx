import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
    it("requires a reason when reasonRequired is set", async () => {
        const onConfirm = vi.fn();
        const user = userEvent.setup();
        render(
            <ConfirmDialog
                open
                title="Revoke run"
                sentence="Revoke this run?"
                reasonRequired
                onConfirm={onConfirm}
                onCancel={() => undefined}
            />,
        );
        expect(screen.getByTestId("confirm-submit")).toBeDisabled();
        await user.type(screen.getByTestId("confirm-reason"), "stop now");
        expect(screen.getByTestId("confirm-submit")).toBeEnabled();
        await user.click(screen.getByTestId("confirm-submit"));
        expect(onConfirm).toHaveBeenCalledWith("stop now");
    });
});
