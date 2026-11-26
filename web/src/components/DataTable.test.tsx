import { render, screen, within } from "@testing-library/react";
import { createColumnHelper } from "@tanstack/react-table";
import { beforeAll, describe, expect, it } from "vitest";

import { DataTable } from "./DataTable";

type Row = { id: number; name: string };

const columnHelper = createColumnHelper<Row>();
const columns = [
    columnHelper.accessor("id", { header: "ID", size: 80 }),
    columnHelper.accessor("name", { header: "Name", size: 200 }),
];

beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
        configurable: true,
        value: 420,
    });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
        configurable: true,
        value: 800,
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
        configurable: true,
        value: 420,
    });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
        configurable: true,
        value: 800,
    });
});

describe("DataTable", () => {
    it("virtualizes 10,000 rows to fewer than 100 DOM rows", () => {
        const data = Array.from({ length: 10_000 }, (_, id) => ({
            id,
            name: `row-${id}`,
        }));

        const { container } = render(
            <DataTable data={data} columns={columns} height={420} aria-label="Rows" />,
        );

        const viewport = screen.getByTestId("data-table-viewport");
        const bodyRows = within(viewport).getAllByRole("row");
        expect(bodyRows.length).toBeLessThan(100);
        expect(bodyRows.length).toBeGreaterThan(0);
        expect(container.querySelectorAll("tbody tr").length).toBeLessThan(100);
        expect(screen.getByLabelText("Rows")).toBeInTheDocument();
        const bodyTable = screen
            .getByTestId("data-table-viewport")
            .querySelector("table");
        expect(bodyTable).toHaveAttribute("aria-rowcount", "10000");
    });

    it("shows an empty state when there is no data", () => {
        render(<DataTable data={[]} columns={columns} emptyTitle="Nothing here" />);
        expect(screen.getByText("Nothing here")).toBeInTheDocument();
    });
});
