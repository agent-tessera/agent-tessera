import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Kbd } from "./Kbd";

describe("Kbd", () => {
    it("renders the shortcut glyph", () => {
        render(<Kbd>⌘K</Kbd>);
        expect(screen.getByText("⌘K").tagName).toBe("KBD");
    });
});
