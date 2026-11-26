import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { applyTheme, initTheme, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: {
    value: ThemePreference;
    label: string;
    icon: typeof Sun;
}[] = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "System", icon: Monitor },
];

export type ThemeToggleProps = {
    className?: string;
};

/** Cycle or pick light / dark / system; persists to `tessera-theme`. */
export function ThemeToggle({ className }: ThemeToggleProps) {
    const [preference, setPreference] = useState<ThemePreference>("system");

    useEffect(() => {
        setPreference(initTheme());
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const onChange = () => {
            setPreference((current) => {
                applyTheme(current);
                return current;
            });
        };
        media.addEventListener("change", onChange);
        return () => media.removeEventListener("change", onChange);
    }, []);

    const set = (next: ThemePreference) => {
        applyTheme(next);
        setPreference(next);
    };

    return (
        <div
            className={cn("inline-flex items-center gap-1", className)}
            role="group"
            aria-label="Theme"
        >
            {OPTIONS.map(({ value, label, icon: Icon }) => (
                <Button
                    key={value}
                    type="button"
                    size="sm"
                    variant={preference === value ? "default" : "ghost"}
                    aria-pressed={preference === value}
                    aria-label={label}
                    onClick={() => set(value)}
                >
                    <Icon className="size-4" aria-hidden />
                    <span className="sr-only">{label}</span>
                </Button>
            ))}
        </div>
    );
}
