/** localStorage key for the user's explicit light/dark choice. Lives in a
 * plain module (not components/theme-toggle.tsx, which is "use client") so
 * the server-rendered app/layout.tsx can import the real string - a value
 * imported from a client module is only a reference on the server. */
export const THEME_STORAGE_KEY = "verity-theme";
