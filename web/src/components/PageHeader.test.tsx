import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
    it("renders title, breadcrumbs, and actions", () => {
        render(
            <PageHeader
                title="Agents"
                breadcrumbs={[
                    { label: "Overview", href: "/app/overview" },
                    { label: "Agents" },
                ]}
                actions={<button type="button">New</button>}
            />,
        );
        expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
        expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
            "href",
            "/app/overview",
        );
        expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
    });
});
