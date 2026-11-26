import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JsonView } from "./JsonView";

describe("JsonView", () => {
    it("shows the document contents for screen readers and tooling", () => {
        render(<JsonView value={{ id: "abc", effect: "deny" }} />);
        expect(screen.getByTestId("json-view")).toBeInTheDocument();
        expect(screen.getByTestId("json-view-body")).toHaveTextContent("deny");
        expect(screen.getByText(/"id": "abc"/)).toBeInTheDocument();
    });
});
