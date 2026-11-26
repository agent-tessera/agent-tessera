import { ChevronDown, LogOut, Monitor, Moon, Sun, User } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import type { MeResponse } from "@/api/me";
import { logout } from "@/api/logout";
import { Button } from "@/components/ui/button";
import { applyTheme, initTheme, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: {
    value: ThemePreference;
    label: string;
    icon: typeof Sun;
}[] = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "System", icon: Monitor },
];

export type ActorMenuProps = {
    me: MeResponse;
    className?: string;
    /** Icon-only trigger for the collapsed sidebar. */
    compact?: boolean;
};

/** Signed-in actor menu: theme preference and logout. */
export function ActorMenu({ me, className, compact = false }: ActorMenuProps) {
    const [open, setOpen] = useState(false);
    const [preference, setPreference] = useState<ThemePreference>("system");
    const [loggingOut, setLoggingOut] = useState(false);
    const menuId = useId();
    const rootRef = useRef<HTMLDivElement>(null);

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

    useEffect(() => {
        if (!open) {
            return;
        }
        const onPointer = (event: MouseEvent) => {
            if (!rootRef.current?.contains(event.target as Node)) {
                setOpen(false);
            }
        };
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", onPointer);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onPointer);
            document.removeEventListener("keydown", onKey);
        };
    }, [open]);

    const setTheme = (next: ThemePreference) => {
        applyTheme(next);
        setPreference(next);
    };

    const onLogout = async () => {
        setLoggingOut(true);
        await logout(me.csrf);
    };

    return (
        <div ref={rootRef} className={cn("relative", className)}>
            <Button
                type="button"
                variant="ghost"
                size="sm"
                className={cn("max-w-full gap-1", compact && "px-2")}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={menuId}
                aria-label={`Account menu for ${me.actor}`}
                onClick={() => setOpen((value) => !value)}
            >
                <User className="size-4 shrink-0" aria-hidden />
                {compact ? (
                    <span className="sr-only">{me.actor}</span>
                ) : (
                    <>
                        <span className="truncate font-mono text-xs">{me.actor}</span>
                        <ChevronDown
                            className="size-3.5 shrink-0 opacity-70"
                            aria-hidden
                        />
                    </>
                )}
            </Button>
            {open ? (
                <div
                    id={menuId}
                    role="menu"
                    aria-label="Account"
                    className={cn(
                        "border-border bg-panel absolute bottom-full z-40 mb-1 w-56 rounded-md border p-2 shadow-sm",
                        compact ? "left-0" : "right-0",
                    )}
                >
                    <p className="text-muted-foreground px-2 pb-2 text-xs">Theme</p>
                    <div
                        className="flex flex-col gap-0.5"
                        role="group"
                        aria-label="Theme"
                    >
                        {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
                            <button
                                key={value}
                                type="button"
                                role="menuitemradio"
                                aria-checked={preference === value}
                                className={cn(
                                    "hover:bg-muted flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                                    preference === value && "bg-muted",
                                )}
                                onClick={() => setTheme(value)}
                            >
                                <Icon className="size-4" aria-hidden />
                                {label}
                            </button>
                        ))}
                    </div>
                    <div className="border-border my-2 border-t" />
                    <button
                        type="button"
                        role="menuitem"
                        className="hover:bg-muted flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm"
                        disabled={loggingOut}
                        onClick={() => {
                            void onLogout();
                        }}
                    >
                        <LogOut className="size-4" aria-hidden />
                        {loggingOut ? "Logging out…" : "Log out"}
                    </button>
                </div>
            ) : null}
        </div>
    );
}
