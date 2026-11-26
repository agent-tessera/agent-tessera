import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
    it("renders each status with a text label", () => {
        const { rerender } = render(<StatusBadge status="running" />);
        expect(screen.getByText("running")).toBeInTheDocument();
        rerender(<StatusBadge status="error" />);
        expect(screen.getByText("error")).toBeInTheDocument();
        rerender(<StatusBadge status="revoked" />);
        expect(screen.getByText("revoked")).toBeInTheDocument();
        rerender(<StatusBadge status="ok" />);
        expect(screen.getByText("ok")).toBeInTheDocument();
    });
});
