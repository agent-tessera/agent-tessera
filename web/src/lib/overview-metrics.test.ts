import { describe, expect, it } from "vitest";

import {
    approvalsOldestFirst,
    errorRate,
    findDenialSpikes,
    formatDelta,
    formatDuration,
    formatRate,
    formatSpend,
    lowPostureAgents,
    relativeDelta,
    runStatusKind,
    sparklineValues,
    sumSeries,
    sumSpend,
} from "./overview-metrics";

describe("overview metrics", () => {
    it("sums and extracts sparkline values", () => {
        const series = [
            { t: "a", total: 2, deny: 1, require_approval: 0, error: 1 },
            { t: "b", total: 3, deny: 0, require_approval: 1, error: 0 },
        ];
        expect(sumSeries(series, "total")).toBe(5);
        expect(sparklineValues(series, "deny")).toEqual([1, 0]);
        expect(errorRate(series)).toBeCloseTo(0.2);
        expect(errorRate([])).toBe(0);
    });

    it("formats deltas spend rate and duration", () => {
        expect(formatDelta(null)).toBe("—");
        expect(formatDelta(0.25)).toBe("+25%");
        expect(formatDelta(-0.1)).toBe("-10%");
        expect(formatSpend(0)).toBe("$0");
        expect(formatSpend(1.234)).toBe("$1.23");
        expect(formatRate(0)).toBe("0%");
        expect(formatRate(0.125)).toBe("12.5%");
        expect(formatRate(0.05)).toBe("5%");
        expect(formatDuration(450)).toBe("450ms");
        expect(formatDuration(4500)).toBe("4.5s");
        expect(formatDuration(12_500)).toBe("13s");
        expect(formatDuration(125_000)).toBe("2m 5s");
    });

    it("computes relative delta only when previous is non-zero", () => {
        expect(relativeDelta(10, 5)).toBe(1);
        expect(relativeDelta(0, 0)).toBeNull();
        expect(relativeDelta(5, 0)).toBeNull();
    });

    it("detects denial spikes against 24h hourly mean", () => {
        const spikes = findDenialSpikes(
            {
                total: 10,
                items: [
                    { agent_id: "a1", name: "spike-bot", denials: 10 },
                    { agent_id: "a2", name: "calm", denials: 1 },
                ],
            },
            {
                total: 34,
                items: [
                    { agent_id: "a1", name: "spike-bot", denials: 12 },
                    { agent_id: "a2", name: "calm", denials: 22 },
                ],
            },
        );
        expect(spikes.map((s) => s.agent_id)).toEqual(["a1"]);
        expect(spikes[0]?.denials_1h).toBe(10);
    });

    it("treats any last-hour denials as a spike when the 24h mean is zero-ish", () => {
        const spikes = findDenialSpikes(
            {
                total: 1,
                items: [{ agent_id: "a1", name: "spike-bot", denials: 1 }],
            },
            {
                total: 1,
                items: [{ agent_id: "a1", name: "spike-bot", denials: 1 }],
            },
        );
        expect(spikes).toHaveLength(1);
    });

    it("sorts approvals oldest first and filters low posture", () => {
        const sorted = approvalsOldestFirst([
            {
                id: "b",
                agent_id: "a",
                status: "pending",
                created_at: "2026-11-09T12:00:00Z",
            },
            {
                id: "a",
                agent_id: "a",
                status: "pending",
                created_at: "2026-11-09T10:00:00Z",
            },
        ]);
        expect(sorted.map((row) => row.id)).toEqual(["a", "b"]);
        expect(
            lowPostureAgents([
                { id: "1", name: "low", score: 40 },
                { id: "2", name: "ok", score: 80 },
            ]).map((row) => row.id),
        ).toEqual(["1"]);
    });

    it("maps run status and sums spend", () => {
        expect(runStatusKind("completed")).toBe("ok");
        expect(runStatusKind("revoked")).toBe("revoked");
        expect(runStatusKind("running")).toBe("running");
        expect(sumSpend([{ cost_usd: 1.5 }, { cost_usd: 0.5 }])).toBe(2);
    });
});
