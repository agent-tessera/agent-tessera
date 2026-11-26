import { useEffect, useId, useRef } from "react";

import { Kbd } from "@/components/Kbd";

export type KeyboardMapProps = {
    open: boolean;
    onClose: () => void;
};

type ShortcutRow = {
    keys: string[];
    description: string;
};

const SECTIONS: { title: string; rows: ShortcutRow[] }[] = [
    {
        title: "Global",
        rows: [
            { keys: ["⌘", "K"], description: "Open command palette" },
            { keys: ["/"], description: "Open command palette" },
            { keys: ["?"], description: "Show this keyboard map" },
            { keys: ["Esc"], description: "Close palette, map, or drawers" },
        ],
    },
    {
        title: "Go to (press g, then…)",
        rows: [
            { keys: ["g", "o"], description: "Overview" },
            { keys: ["g", "e"], description: "Explorer" },
            { keys: ["g", "t"], description: "Traces" },
            { keys: ["g", "a"], description: "Agents" },
            { keys: ["g", "p"], description: "Approvals" },
            { keys: ["g", "i"], description: "Insights" },
        ],
    },
    {
        title: "Tables (when focused)",
        rows: [
            { keys: ["j"], description: "Move down a row" },
            { keys: ["k"], description: "Move up a row" },
            { keys: ["Enter"], description: "Open the selected row" },
            { keys: ["Esc"], description: "Close the row drawer" },
        ],
    },
    {
        title: "Approvals inbox",
        rows: [
            { keys: ["j"], description: "Next request" },
            { keys: ["k"], description: "Previous request" },
            { keys: ["a"], description: "Approve focused request" },
            { keys: ["d"], description: "Deny (requires confirm)" },
            { keys: ["Enter"], description: "Focus the comment field" },
        ],
    },
];

/** Modal listing console keyboard shortcuts. */
export function KeyboardMap({ open, onClose }: KeyboardMapProps) {
    const titleId = useId();
    const dialogRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
            }
        };
        window.addEventListener("keydown", onKey, true);
        const t = window.setTimeout(() => {
            dialogRef.current?.querySelector<HTMLElement>("button,[tabindex]")?.focus();
        }, 0);
        return () => {
            window.removeEventListener("keydown", onKey, true);
            window.clearTimeout(t);
        };
    }, [open, onClose]);

    if (!open) {
        return null;
    }

    return (
        <div
            className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
            role="presentation"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) {
                    onClose();
                }
            }}
        >
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="border-border bg-panel w-full max-w-md overflow-hidden rounded-lg border shadow-lg"
            >
                <div className="border-border flex items-center justify-between border-b px-4 py-3">
                    <h2 id={titleId} className="text-sm font-semibold">
                        Keyboard shortcuts
                    </h2>
                    <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground text-sm"
                        onClick={onClose}
                    >
                        Close
                    </button>
                </div>
                <div className="max-h-[min(28rem,70vh)] space-y-4 overflow-auto p-4">
                    {SECTIONS.map((section) => (
                        <section key={section.title}>
                            <h3 className="text-muted-foreground mb-2 text-[11px] font-medium tracking-wide uppercase">
                                {section.title}
                            </h3>
                            <ul className="space-y-1.5">
                                {section.rows.map((row) => (
                                    <li
                                        key={`${section.title}-${row.description}`}
                                        className="flex items-center justify-between gap-3 text-sm"
                                    >
                                        <span>{row.description}</span>
                                        <span className="inline-flex items-center gap-1">
                                            {row.keys.map((key) => (
                                                <Kbd key={key}>{key}</Kbd>
                                            ))}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    ))}
                </div>
            </div>
        </div>
    );
}
