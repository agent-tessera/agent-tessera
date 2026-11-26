import { describe, expect, it } from "vitest";

import {
    denialAgentRows,
    denialRuleRows,
    formatEnergyJ,
    prepareStackedSeries,
    spendRows,
    stackSpendBuckets,
    sumSpend,
    weakestComponentLabel,
} from "./insights-metrics";

describe("spendRows / sumSpend", () => {
    it("sums agent spend and filters by environment set", () => {
        const data = {
            group_by: "agent",
            items: [
                { agent_id: "a1", name: "alpha", cost_usd: 1.5 },
                { agent_id: "a2", name: "beta", cost_usd: 0.25 },
            ],
        };
        expect(sumSpend(data.items)).toBe(1.75);
        expect(spendRows(data, new Set(["a2"]))).toEqual([
            { key: "a2", label: "beta", cost_usd: 0.25 },
        ]);
    });

    it("labels model rows", () => {
        expect(
            spendRows({
                group_by: "model",
                items: [{ model: "openai/gpt-4o-mini", cost_usd: 0.02 }],
            }),
        ).toEqual([
            {
                key: "openai/gpt-4o-mini",
                label: "openai/gpt-4o-mini",
                cost_usd: 0.02,
            },
        ]);
    });
});

describe("denial grouping", () => {
    it("groups deny events by rule", () => {
        const rows = denialRuleRows([
            { id: "1", effect: "deny", rule: "agent:tools.deny[0]", agent: "a1" },
            { id: "2", effect: "deny", rule: "agent:tools.deny[0]", agent: "a1" },
            { id: "3", effect: "deny", rule: "baseline:x", agent: "a2" },
            { id: "4", effect: "allow", rule: "agent:tools.allow[0]", agent: "a1" },
        ]);
        expect(rows).toEqual([
            { key: "agent:tools.deny[0]", label: "agent:tools.deny[0]", denials: 2 },
            { key: "baseline:x", label: "baseline:x", denials: 1 },
        ]);
    });

    it("filters denial agent rows", () => {
        expect(
            denialAgentRows(
                {
                    total: 3,
                    items: [
                        { agent_id: "a1", name: "alpha", denials: 2 },
                        { agent_id: "a2", name: "beta", denials: 1 },
                    ],
                },
                new Set(["a1"]),
            ),
        ).toEqual([{ key: "a1", label: "alpha", denials: 2 }]);
    });
});

describe("stack helpers", () => {
    it("builds stacked series with Other bucket", () => {
        const stacked = stackSpendBuckets(
            [{ label: "t0" }, { label: "t1" }],
            [
                {
                    group_by: "agent",
                    items: [
                        { agent_id: "a1", name: "a", cost_usd: 5 },
                        { agent_id: "a2", name: "b", cost_usd: 4 },
                        { agent_id: "a3", name: "c", cost_usd: 3 },
                        { agent_id: "a4", name: "d", cost_usd: 2 },
                        { agent_id: "a5", name: "e", cost_usd: 1 },
                        { agent_id: "a6", name: "f", cost_usd: 0.5 },
                    ],
                },
                {
                    group_by: "agent",
                    items: [{ agent_id: "a1", name: "a", cost_usd: 1 }],
                },
            ],
            "agent",
        );
        const prepared = prepareStackedSeries(stacked, 5);
        expect(prepared.keys).toContain("Other");
        expect(prepared.keys).toHaveLength(6);
        expect(prepared.points[0]?.total).toBe(15.5);
    });
});

describe("footprint / posture labels", () => {
    it("formats energy and names weakest component", () => {
        expect(formatEnergyJ(1500)).toBe("1.50 kJ");
        expect(formatEnergyJ(null)).toBe("—");
        expect(
            weakestComponentLabel({
                id: "a1",
                name: "alpha",
                score: 40,
                coverage: 0,
                components: {
                    owner: 20,
                    guardrails_declared: 20,
                    coverage: 0,
                    denial_rate: 15,
                    ungoverned: 15,
                    probes: 0,
                },
            }),
        ).toBe("Coverage");
    });
});
