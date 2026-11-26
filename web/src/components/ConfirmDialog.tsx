import {
    useEffect,
    useId,
    useRef,
    useState,
    type FormEvent,
    type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const REASON_MAX = 500;

export type ConfirmDialogProps = {
    open: boolean;
    title: string;
    /** Confirmation sentence shown above the reason field. */
    sentence: string;
    confirmLabel?: string;
    cancelLabel?: string;
    /** When true, Confirm stays disabled until reason is non-empty. */
    reasonRequired?: boolean;
    reasonLabel?: string;
    /** Destructive styling for the confirm button. */
    destructive?: boolean;
    onConfirm: (reason: string) => void;
    onCancel: () => void;
    children?: ReactNode;
    className?: string;
};

/**
 * Modal confirm with an optional operator reason (max 500 chars).
 *
 * Used for destructive console actions (revoke run, lifecycle, …).
 */
export function ConfirmDialog({
    open,
    title,
    sentence,
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    reasonRequired = false,
    reasonLabel = "Reason",
    destructive = false,
    onConfirm,
    onCancel,
    children,
    className,
}: ConfirmDialogProps) {
    const titleId = useId();
    const reasonId = useId();
    const dialogRef = useRef<HTMLDivElement>(null);
    const [reason, setReason] = useState("");

    useEffect(() => {
        if (!open) {
            return;
        }
        setReason("");
        const root = dialogRef.current;
        const focusable = root?.querySelector<HTMLElement>(
            "textarea,button,[href],input,[tabindex]:not([tabindex='-1'])",
        );
        focusable?.focus();
    }, [open]);

    useEffect(() => {
        if (!open) {
            return;
        }
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onCancel();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onCancel]);

    if (!open) {
        return null;
    }

    const trimmed = reason.trim();
    const canConfirm = !reasonRequired || trimmed.length > 0;

    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (!canConfirm) {
            return;
        }
        onConfirm(trimmed);
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            data-testid="confirm-dialog-root"
            role="presentation"
        >
            <button
                type="button"
                className="bg-foreground/30 absolute inset-0 cursor-default border-0"
                aria-label="Close confirm backdrop"
                data-testid="confirm-dialog-backdrop"
                onClick={onCancel}
            />
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                data-testid="confirm-dialog"
                className={cn(
                    "border-border bg-panel relative z-10 w-full max-w-md rounded-md border p-4 shadow-sm",
                    className,
                )}
            >
                <form className="space-y-4" onSubmit={submit}>
                    <div className="space-y-1">
                        <h2 id={titleId} className="text-sm font-semibold">
                            {title}
                        </h2>
                        <p
                            className="text-muted-foreground text-sm"
                            data-testid="confirm-sentence"
                        >
                            {sentence}
                        </p>
                    </div>
                    {children}
                    <div className="space-y-1.5">
                        <label
                            htmlFor={reasonId}
                            className="text-muted-foreground text-xs tracking-wide uppercase"
                        >
                            {reasonLabel}
                            {reasonRequired ? "" : " (optional)"}
                        </label>
                        <textarea
                            id={reasonId}
                            name="reason"
                            rows={3}
                            maxLength={REASON_MAX}
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            className="border-border bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
                            data-testid="confirm-reason"
                        />
                        <p className="text-muted-foreground text-xs tabular-nums">
                            {reason.length}/{REASON_MAX}
                        </p>
                    </div>
                    <div className="flex justify-end gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            data-testid="confirm-cancel"
                            onClick={onCancel}
                        >
                            {cancelLabel}
                        </Button>
                        <Button
                            type="submit"
                            size="sm"
                            disabled={!canConfirm}
                            data-testid="confirm-submit"
                            className={
                                destructive
                                    ? "border-status-error text-status-error-fg hover:bg-status-error-bg border"
                                    : undefined
                            }
                            variant={destructive ? "outline" : "default"}
                        >
                            {confirmLabel}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}
