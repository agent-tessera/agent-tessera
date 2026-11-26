import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EffectBadge } from "./EffectBadge";

describe("EffectBadge", () => {
    it("renders allow with text and icon", () => {
        const { container } = render(<EffectBadge effect="allow" />);
        expect(screen.getByText("allow")).toBeInTheDocument();
        expect(container.querySelector("svg")).not.toBeNull();
    });

    it("renders deny and require_approval labels", () => {
        const { rerender } = render(<EffectBadge effect="deny" />);
        expect(screen.getByText("deny")).toBeInTheDocument();
        rerender(<EffectBadge effect="require_approval" />);
        expect(screen.getByText("require approval")).toBeInTheDocument();
    });
});
