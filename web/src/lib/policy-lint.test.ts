import { describe, expect, it } from "vitest";

import { policyErrorDiagnostics } from "./policy-lint";

describe("policyErrorDiagnostics", () => {
    it("extracts PyYAML line marks", () => {
        const error =
            'while scanning a simple key\n  in "<unicode string>", line 5, column 3:\n      [\n      ^';
        expect(policyErrorDiagnostics(error)).toEqual([{ line: 5, message: error }]);
    });

    it("keeps every distinct line from multiline YAML errors", () => {
        const error =
            'while scanning a simple key\n  in "<unicode string>", line 5, column 3:\n' +
            "could not find expected ':'\n  in \"<unicode string>\", line 6, column 1:";
        expect(policyErrorDiagnostics(error)).toEqual([
            { line: 5, message: error },
            { line: 6, message: error },
        ]);
    });

    it("falls back to line 1 when no mark is present", () => {
        expect(policyErrorDiagnostics("kind must be AgentPolicy")).toEqual([
            { line: 1, message: "kind must be AgentPolicy" },
        ]);
    });

    it("returns empty for blank input", () => {
        expect(policyErrorDiagnostics("   ")).toEqual([]);
    });
});
