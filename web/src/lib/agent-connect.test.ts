import { describe, expect, it } from "vitest";

import type { AgentInstance } from "@/api/agents";
import {
    CONNECT_PYTHON_SNIPPET,
    connectEnvSnippet,
    firstConnectedShortId,
} from "./agent-connect";

function instance(id: string, revoked = false): AgentInstance {
    return {
        id,
        host: "host",
        runtime: {},
        started_at: "2026-11-10T00:00:00Z",
        last_heartbeat_at: "2026-11-10T00:00:00Z",
        revoked,
    };
}

describe("firstConnectedShortId", () => {
    it("returns null when there are no live instances", () => {
        expect(firstConnectedShortId([])).toBeNull();
        expect(firstConnectedShortId([instance("aabbccddeeff0011", true)])).toBeNull();
    });

    it("returns the first eight chars of the first live instance", () => {
        expect(
            firstConnectedShortId([
                instance("deadbeef01234567", true),
                instance("abcdef0123456789"),
            ]),
        ).toBe("abcdef01");
    });
});

describe("connect snippets", () => {
    it("builds env lines without a trailing slash on the URL", () => {
        expect(connectEnvSnippet("http://127.0.0.1:18700", "tsr_id_secret")).toBe(
            "TESSERA_URL=http://127.0.0.1:18700\nTESSERA_AGENT_TOKEN=tsr_id_secret",
        );
    });

    it("keeps the classic six-line Python bootstrap", () => {
        expect(CONNECT_PYTHON_SNIPPET).toContain("from agent_tessera import Tessera");
        expect(CONNECT_PYTHON_SNIPPET.split("\n").length).toBeGreaterThanOrEqual(6);
    });
});
