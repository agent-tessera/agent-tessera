import { describe, expect, it } from "vitest";

import { fillTemplatePreview, parseWelcomeStep, runIdFromEvent } from "./welcome";

describe("welcome helpers", () => {
    it("parses welcome steps and defaults to 1", () => {
        expect(parseWelcomeStep(2)).toBe(2);
        expect(parseWelcomeStep("3")).toBe(3);
        expect(parseWelcomeStep("4")).toBe(4);
        expect(parseWelcomeStep(1)).toBe(1);
        expect(parseWelcomeStep("nope")).toBe(1);
        expect(parseWelcomeStep(undefined)).toBe(1);
    });

    it("fills AGENT and ENV placeholders in template YAML", () => {
        const raw = [
            "metadata:",
            "  agent: AGENT",
            "  environment: ENV",
            "spec: {}",
        ].join("\n");
        expect(fillTemplatePreview(raw, "welcome-probe", "dev")).toBe(
            [
                "metadata:",
                "  agent: welcome-probe",
                "  environment: dev",
                "spec: {}",
            ].join("\n"),
        );
    });

    it("resolves a run id from event payload fields", () => {
        expect(runIdFromEvent({ run: "r1" })).toBe("r1");
        expect(runIdFromEvent({ task: "t1" })).toBe("t1");
        expect(runIdFromEvent({ run_id: "x1" })).toBe("x1");
        expect(runIdFromEvent({})).toBeNull();
    });
});
