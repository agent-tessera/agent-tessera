import { describe, expect, it } from "vitest";

import {
    agentsKeys,
    approvalsKeys,
    auditKeys,
    dashboardsKeys,
    eventsKeys,
    meKeys,
    ownersKeys,
    policyTemplateKeys,
    postureKeys,
    runsKeys,
    searchKeys,
    settingsKeys,
} from "./keys";

describe("query keys", () => {
    it("namespaces each resource", () => {
        expect(meKeys.all).toEqual(["me"]);
        expect(eventsKeys.list({ agent: "a" })).toEqual([
            "events",
            "list",
            { agent: "a" },
        ]);
        expect(runsKeys.trace("r1")).toEqual(["runs", "trace", "r1"]);
        expect(runsKeys.effects("r1")).toEqual(["runs", "effects", "r1"]);
        expect(agentsKeys.instances("ag1")).toEqual(["agents", "instances", "ag1"]);
        expect(ownersKeys.list({ limit: 200 })).toEqual([
            "owners",
            "list",
            { limit: 200 },
        ]);
        expect(approvalsKeys.detail("ap1")).toEqual(["approvals", "detail", "ap1"]);
        expect(searchKeys.query("refund")).toEqual(["search", "refund", {}]);
        expect(postureKeys.summary()).toEqual(["posture", "summary", {}]);
        expect(dashboardsKeys.summary()).toEqual(["dashboards", "summary", {}]);
        expect(dashboardsKeys.denials({ window: "1h" })).toEqual([
            "dashboards",
            "denials",
            { window: "1h" },
        ]);
        expect(auditKeys.verify()).toEqual(["audit", "verify"]);
        expect(settingsKeys.alerts()).toEqual(["settings", "alerts"]);
        expect(settingsKeys.adminKeys()).toEqual(["settings", "admin-keys"]);
        expect(settingsKeys.baseline("org")).toEqual(["settings", "baseline", "org"]);
        expect(policyTemplateKeys.list()).toEqual(["policy-templates", "list"]);
    });
});
