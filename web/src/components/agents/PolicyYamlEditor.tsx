import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { yaml } from "@codemirror/lang-yaml";
import {
    bracketMatching,
    defaultHighlightStyle,
    syntaxHighlighting,
} from "@codemirror/language";
import { lintGutter, setDiagnostics, type Diagnostic } from "@codemirror/lint";
import { EditorState } from "@codemirror/state";
import {
    EditorView,
    highlightActiveLine,
    keymap,
    lineNumbers,
    placeholder,
} from "@codemirror/view";
import { useEffect, useRef } from "react";

import type { PolicyLintDiagnostic } from "@/lib/policy-lint";
import { cn } from "@/lib/utils";

export type PolicyYamlEditorProps = {
    value: string;
    onChange: (value: string) => void;
    diagnostics?: PolicyLintDiagnostic[];
    className?: string;
    /** Accessible name for the CodeMirror textbox (axe `aria-input-field-name`). */
    "aria-label"?: string;
};

/**
 * CodeMirror 6 YAML editor with server-driven lint markers.
 *
 * Loaded only from the Policy tab (lazy parent) so the CM chunk stays off
 * the initial console bundle.
 */
export function PolicyYamlEditor({
    value,
    onChange,
    diagnostics = [],
    className,
    "aria-label": ariaLabel = "Policy YAML",
}: PolicyYamlEditorProps) {
    const hostRef = useRef<HTMLDivElement | null>(null);
    const viewRef = useRef<EditorView | null>(null);
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;

    useEffect(() => {
        const host = hostRef.current;
        if (!host) {
            return;
        }
        const view = new EditorView({
            parent: host,
            state: EditorState.create({
                doc: value,
                extensions: [
                    lineNumbers(),
                    highlightActiveLine(),
                    history(),
                    bracketMatching(),
                    lintGutter(),
                    yaml(),
                    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
                    keymap.of([...defaultKeymap, ...historyKeymap]),
                    placeholder("Paste an AgentPolicy YAML document"),
                    EditorView.contentAttributes.of({
                        "aria-label": ariaLabel,
                    }),
                    EditorView.theme({
                        "&": {
                            fontSize: "13px",
                            height: "100%",
                        },
                        ".cm-scroller": {
                            fontFamily:
                                'var(--font-mono, "IBM Plex Mono", ui-monospace, monospace)',
                            lineHeight: "1.45",
                        },
                        ".cm-content": {
                            minHeight: "16rem",
                            padding: "0.75rem 0",
                        },
                        "&.cm-focused": {
                            outline: "none",
                        },
                        ".cm-gutters": {
                            backgroundColor: "transparent",
                            border: "none",
                            color: "var(--muted-foreground)",
                        },
                        ".cm-activeLine": {
                            backgroundColor:
                                "color-mix(in oklab, var(--muted) 55%, transparent)",
                        },
                        ".cm-activeLineGutter": {
                            backgroundColor: "transparent",
                        },
                    }),
                    EditorView.updateListener.of((update) => {
                        if (update.docChanged) {
                            onChangeRef.current(update.state.doc.toString());
                        }
                    }),
                ],
            }),
        });
        viewRef.current = view;
        return () => {
            view.destroy();
            viewRef.current = null;
        };
        // Mount once; value sync is handled below.
        // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional
    }, []);

    useEffect(() => {
        const view = viewRef.current;
        if (!view) {
            return;
        }
        const current = view.state.doc.toString();
        if (current === value) {
            return;
        }
        view.dispatch({
            changes: { from: 0, to: current.length, insert: value },
        });
    }, [value]);

    useEffect(() => {
        const view = viewRef.current;
        if (!view) {
            return;
        }
        const cmDiagnostics: Diagnostic[] = diagnostics.map((item) => {
            const lineCount = view.state.doc.lines;
            const lineNo = Math.min(Math.max(item.line, 1), lineCount || 1);
            const line = view.state.doc.line(lineNo);
            return {
                from: line.from,
                to: line.to,
                severity: "error",
                message: item.message,
            };
        });
        view.dispatch(setDiagnostics(view.state, cmDiagnostics));
    }, [diagnostics]);

    return (
        <div
            ref={hostRef}
            className={cn(
                "border-border bg-panel focus-within:ring-ring max-h-[32rem] min-h-[16rem] overflow-auto rounded-md border focus-within:ring-2",
                className,
            )}
            data-testid="policy-yaml-editor"
        />
    );
}
