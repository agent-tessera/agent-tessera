import { describe, expect, it } from "vitest";

import { parseSseBlock } from "./event-stream";

describe("parseSseBlock", () => {
    it("parses id, event, and data lines", () => {
        expect(parseSseBlock('id: cur\nevent: lag\ndata: {"ok":true}')).toEqual({
            event: "lag",
            data: '{"ok":true}',
            id: "cur",
        });
    });

    it("joins multi-line data and ignores comments", () => {
        expect(parseSseBlock(": keepalive\ndata: one\ndata: two")).toEqual({
            event: "message",
            data: "one\ntwo",
            id: null,
        });
    });

    it("returns null for empty comment-only blocks", () => {
        expect(parseSseBlock(": keepalive")).toBeNull();
        expect(parseSseBlock("")).toBeNull();
    });
});
