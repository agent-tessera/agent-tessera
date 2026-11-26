import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Mono } from "./Mono";

describe("Mono", () => {
    it("copies its value on click", async () => {
        const user = userEvent.setup();
        render(<Mono>abc123</Mono>);
        const button = screen.getByRole("button", { name: "Copy abc123" });
        await user.click(button);
        expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
        await expect(navigator.clipboard.readText()).resolves.toBe("abc123");
    });
});
