/** Settings section nav for `/app/settings/*`. */

export type SettingsSectionId =
    "owners" | "alerts" | "webhooks" | "baselines" | "custody" | "admin-keys" | "audit";

export type SettingsSection = {
    id: SettingsSectionId;
    label: string;
    description: string;
    to:
        | "/settings/owners"
        | "/settings/alerts"
        | "/settings/webhooks"
        | "/settings/baselines"
        | "/settings/custody"
        | "/settings/admin-keys"
        | "/settings/audit";
};

/** Ordered settings sections (directory, ops, then evidence). */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
    {
        id: "owners",
        label: "Owners",
        description: "Users and teams that own agents.",
        to: "/settings/owners",
    },
    {
        id: "alerts",
        label: "Alerts",
        description: "Threshold rules that notify or suspend agents.",
        to: "/settings/alerts",
    },
    {
        id: "webhooks",
        label: "Webhooks",
        description: "Signed delivery endpoints, including Send test.",
        to: "/settings/webhooks",
    },
    {
        id: "baselines",
        label: "Baselines",
        description: "Org baseline YAML documents merged into agent policies.",
        to: "/settings/baselines",
    },
    {
        id: "custody",
        label: "Custody",
        description: "Provider keys encrypted by the server.",
        to: "/settings/custody",
    },
    {
        id: "admin-keys",
        label: "Admin keys",
        description: "Operator credentials; secrets are shown once.",
        to: "/settings/admin-keys",
    },
    {
        id: "audit",
        label: "Audit",
        description: "Server hash-chained audit log and verify status.",
        to: "/settings/audit",
    },
] as const;
