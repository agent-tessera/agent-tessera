import { describe, expect, it } from "vitest";

import { permissiveKinds } from "./permissive-policy";

describe("permissiveKinds", () => {
    it("returns empty for blank policy", () => {
        expect(permissiveKinds("")).toEqual([]);
        expect(permissiveKinds("   ")).toEqual([]);
    });

    it("detects starred allow lists", () => {
        const yaml = `
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  name: wide
spec:
  models:
    allow:
      - "*"
  tools:
    allow:
      - files.read
  data:
    allow: ["*"]
`;
        expect(permissiveKinds(yaml)).toEqual(["model", "data"]);
    });

    it("ignores non-star allows", () => {
        const yaml = `
spec:
  tools:
    allow:
      - shell.exec
  models:
    allow:
      - gpt-4o
`;
        expect(permissiveKinds(yaml)).toEqual([]);
    });
});
