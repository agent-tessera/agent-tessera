import type { ReactNode } from "react";

export type SettingsFieldProps = {
    label: string;
    htmlFor: string;
    error?: string;
    hint?: string;
    children: ReactNode;
};

/** Label + control + optional 422 field error for settings forms. */
export function SettingsField({
    label,
    htmlFor,
    error,
    hint,
    children,
}: SettingsFieldProps) {
    return (
        <div className="space-y-1.5">
            <label
                htmlFor={htmlFor}
                className="text-muted-foreground text-xs tracking-wide uppercase"
            >
                {label}
            </label>
            {children}
            {hint && !error ? (
                <p className="text-muted-foreground text-xs">{hint}</p>
            ) : null}
            {error ? (
                <p
                    className="text-status-error text-xs"
                    role="alert"
                    data-testid={`field-error-${htmlFor}`}
                >
                    {error}
                </p>
            ) : null}
        </div>
    );
}
