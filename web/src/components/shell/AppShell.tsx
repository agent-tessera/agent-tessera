import { Outlet } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import type { MeResponse } from "@/api/me";
import { readSidebarCollapsed, writeSidebarCollapsed } from "@/lib/sidebar";

import { CommandPalette } from "./CommandPalette";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export type AppShellProps = {
    me: MeResponse;
};

const NARROW_QUERY = "(max-width: 767px)";

/** Authenticated console chrome: sidebar + top bar + page outlet. */
export function AppShell({ me }: AppShellProps) {
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        const media = window.matchMedia(NARROW_QUERY);
        const sync = () => {
            if (media.matches) {
                setCollapsed(true);
            } else {
                setCollapsed(readSidebarCollapsed());
            }
        };
        sync();
        media.addEventListener("change", sync);
        return () => media.removeEventListener("change", sync);
    }, []);

    const onCollapsedChange = (next: boolean) => {
        writeSidebarCollapsed(next);
        setCollapsed(next);
    };

    return (
        <div className="bg-background text-foreground flex min-h-screen overflow-x-clip">
            <Sidebar
                me={me}
                collapsed={collapsed}
                onCollapsedChange={onCollapsedChange}
            />
            <div className="flex min-w-0 flex-1 flex-col">
                <TopBar />
                <main className="min-h-0 flex-1 overflow-auto p-4 sm:p-6">
                    <Outlet />
                </main>
            </div>
            <CommandPalette />
            <KeyboardShortcuts />
        </div>
    );
}
