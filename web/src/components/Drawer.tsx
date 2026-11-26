import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { CLOSE_DRAWERS_EVENT } from "@/lib/shell-events";
import { cn } from "@/lib/utils";

const MIN_WIDTH = 320;
const MAX_WIDTH = 720;
const DEFAULT_WIDTH = 420;
const WIDTH_KEY = "tessera-drawer-width";

export type DrawerProps = {
    open: boolean;
    title: string;
    onClose: () => void;
    children: ReactNode;
    footer?: ReactNode;
    className?: string;
    /** Accessible label for the panel. */
    "aria-label"?: string;
};

function readStoredWidth(): number {
    if (typeof localStorage === "undefined") {
        return DEFAULT_WIDTH;
    }
    const raw = localStorage.getItem(WIDTH_KEY);
    const parsed = raw ? Number(raw) : NaN;
    if (!Number.isFinite(parsed)) {
        return DEFAULT_WIDTH;
    }
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, parsed));
}

/**
 * Resizable right-side detail panel.
 *
 * Listens for {@link CLOSE_DRAWERS_EVENT} (Esc from the shell) and calls
 * `onClose`. Width persists in `localStorage`.
 */
export function Drawer({
    open,
    title,
    onClose,
    children,
    footer,
    className,
    "aria-label": ariaLabel,
}: DrawerProps) {
    const titleId = useId();
    const [width, setWidth] = useState(DEFAULT_WIDTH);
    const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

    useEffect(() => {
        setWidth(readStoredWidth());
    }, []);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onCloseEvent = () => {
            onClose();
        };
        window.addEventListener(CLOSE_DRAWERS_EVENT, onCloseEvent);
        return () => window.removeEventListener(CLOSE_DRAWERS_EVENT, onCloseEvent);
    }, [open, onClose]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [open, onClose]);

    useEffect(() => {
        const onMove = (event: PointerEvent) => {
            const drag = dragRef.current;
            if (!drag) {
                return;
            }
            const next = Math.min(
                MAX_WIDTH,
                Math.max(MIN_WIDTH, drag.startWidth + (drag.startX - event.clientX)),
            );
            setWidth(next);
        };
        const onUp = () => {
            if (!dragRef.current) {
                return;
            }
            dragRef.current = null;
            setWidth((current) => {
                if (typeof localStorage !== "undefined") {
                    localStorage.setItem(WIDTH_KEY, String(current));
                }
                return current;
            });
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        return () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
        };
    }, []);

    if (!open) {
        return null;
    }

    return (
        <div
            className="fixed inset-0 z-40 flex justify-end"
            data-testid="drawer-root"
            role="presentation"
        >
            <button
                type="button"
                className="bg-foreground/20 absolute inset-0 cursor-default border-0"
                aria-label="Close drawer backdrop"
                data-testid="drawer-backdrop"
                onClick={onClose}
            />
            <aside
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                aria-label={ariaLabel ?? title}
                data-testid="drawer"
                className={cn(
                    "border-border bg-panel relative z-10 flex h-full flex-col border-l shadow-sm",
                    className,
                )}
                style={{ width }}
            >
                <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize drawer"
                    data-testid="drawer-resize"
                    className="hover:bg-primary/40 absolute top-0 left-0 h-full w-1.5 cursor-col-resize"
                    onPointerDown={(event) => {
                        event.preventDefault();
                        dragRef.current = { startX: event.clientX, startWidth: width };
                    }}
                />
                <header className="border-border flex items-start justify-between gap-3 border-b px-4 py-3">
                    <h2 id={titleId} className="text-sm font-semibold">
                        {title}
                    </h2>
                    <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground rounded-md px-2 py-1 text-sm"
                        data-testid="drawer-close"
                        onClick={onClose}
                    >
                        Close
                    </button>
                </header>
                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                    {children}
                </div>
                {footer ? (
                    <footer className="border-border border-t px-4 py-3">
                        {footer}
                    </footer>
                ) : null}
            </aside>
        </div>
    );
}
