import { useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type JsonViewProps = {
    value: unknown;
    className?: string;
    /** Initially expand nested objects (default true for the root). */
    defaultExpanded?: boolean;
};

type NodeProps = {
    name?: string;
    value: unknown;
    depth: number;
    defaultExpanded: boolean;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function preview(value: unknown): string {
    if (value === null) {
        return "null";
    }
    if (Array.isArray(value)) {
        return `Array(${value.length})`;
    }
    if (isPlainObject(value)) {
        return `{${Object.keys(value).length}}`;
    }
    if (typeof value === "string") {
        return JSON.stringify(value);
    }
    return String(value);
}

function JsonNode({ name, value, depth, defaultExpanded }: NodeProps): ReactNode {
    const expandable = Array.isArray(value) || isPlainObject(value);
    const [open, setOpen] = useState(defaultExpanded && depth < 2);

    if (!expandable) {
        return (
            <div className="font-mono text-[12px] leading-5">
                {name !== undefined ? (
                    <span className="text-muted-foreground">{name}: </span>
                ) : null}
                <span className="text-foreground">{preview(value)}</span>
            </div>
        );
    }

    const entries = Array.isArray(value)
        ? value.map((item, index) => [String(index), item] as const)
        : Object.entries(value);

    return (
        <div className="font-mono text-[12px] leading-5">
            <button
                type="button"
                className="hover:bg-muted/60 -ml-1 rounded px-1 text-left"
                aria-expanded={open}
                onClick={() => setOpen((current) => !current)}
            >
                <span className="text-muted-foreground mr-1">{open ? "▾" : "▸"}</span>
                {name !== undefined ? (
                    <span className="text-muted-foreground">{name}: </span>
                ) : null}
                <span className="text-muted-foreground">
                    {Array.isArray(value) ? "[" : "{"}
                    {!open ? ` ${preview(value)} ` : ""}
                    {Array.isArray(value) ? "]" : "}"}
                </span>
            </button>
            {open ? (
                <div className="border-border ml-3 border-l pl-3">
                    {entries.map(([key, child]) => (
                        <JsonNode
                            key={`${depth}-${key}`}
                            name={key}
                            value={child}
                            depth={depth + 1}
                            defaultExpanded={defaultExpanded}
                        />
                    ))}
                </div>
            ) : null}
        </div>
    );
}

async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/**
 * Collapsible JSON tree with a one-click copy of the full document.
 */
export function JsonView({ value, className, defaultExpanded = true }: JsonViewProps) {
    const [copied, setCopied] = useState(false);
    const text = useMemo(() => {
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return String(value);
        }
    }, [value]);

    return (
        <div
            className={cn("border-border bg-muted/40 rounded-md border", className)}
            data-testid="json-view"
        >
            <div className="border-border flex items-center justify-between border-b px-2 py-1">
                <span className="text-muted-foreground text-xs font-medium">JSON</span>
                <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    data-testid="json-view-copy"
                    onClick={() => {
                        void copyText(text).then((ok) => {
                            if (!ok) {
                                return;
                            }
                            setCopied(true);
                            window.setTimeout(() => setCopied(false), 1200);
                        });
                    }}
                >
                    {copied ? "Copied" : "Copy"}
                </Button>
            </div>
            <div
                className="max-h-[28rem] overflow-auto p-2"
                data-testid="json-view-body"
            >
                <JsonNode value={value} depth={0} defaultExpanded={defaultExpanded} />
                <pre className="sr-only">{text}</pre>
            </div>
        </div>
    );
}
