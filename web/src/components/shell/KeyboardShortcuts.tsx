import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { G_SHORTCUTS } from "@/lib/palette-actions";
import { requestCloseDrawers } from "@/lib/shell-events";

import { KeyboardMap } from "./KeyboardMap";

function isEditableTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) {
        return false;
    }
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return true;
    }
    return target.isContentEditable;
}

/**
 * Console `g` chords, `?` keyboard map, and Esc drawer close.
 *
 * Mounted once in the authenticated shell alongside the command palette.
 */
export function KeyboardShortcuts() {
    const navigate = useNavigate();
    const [mapOpen, setMapOpen] = useState(false);
    const [pendingG, setPendingG] = useState(false);

    useEffect(() => {
        if (!pendingG) {
            return;
        }
        const timer = window.setTimeout(() => setPendingG(false), 1000);
        return () => window.clearTimeout(timer);
    }, [pendingG]);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.defaultPrevented) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.altKey) {
                return;
            }

            if (event.key === "Escape") {
                if (mapOpen) {
                    return;
                }
                requestCloseDrawers();
                return;
            }

            if (isEditableTarget(event.target)) {
                setPendingG(false);
                return;
            }

            if (event.key === "?" || (event.shiftKey && event.key === "/")) {
                event.preventDefault();
                setMapOpen(true);
                setPendingG(false);
                return;
            }

            if (pendingG) {
                const path = G_SHORTCUTS[event.key.toLowerCase()];
                setPendingG(false);
                if (path) {
                    event.preventDefault();
                    void navigate({ to: path, search: (prev) => prev });
                }
                return;
            }

            if (event.key === "g" || event.key === "G") {
                event.preventDefault();
                setPendingG(true);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [mapOpen, navigate, pendingG]);

    return <KeyboardMap open={mapOpen} onClose={() => setMapOpen(false)} />;
}
