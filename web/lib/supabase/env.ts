/** Read per request (not inlined at build), so one image serves every deploy. */
export function supabaseUrl(): string {
  const url = process.env.SUPABASE_URL;
  if (!url) throw new Error("SUPABASE_URL is not set");
  return url;
}

export function supabaseKey(): string {
  const key = process.env.SUPABASE_PUBLISHABLE_KEY;
  if (!key) throw new Error("SUPABASE_PUBLISHABLE_KEY is not set");
  return key;
}

/** Owner/allowlist, mirroring app/auth.py. Fails closed when unset. */
export function isAllowed(userId: string): boolean {
  const ids = `${process.env.OWNER_USER_ID ?? ""},${process.env.ALLOWED_USER_IDS ?? ""}`
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return ids.includes(userId);
}

/** Forced onto every auth cookie in setAll (not via @supabase/ssr's
 * cookieOptions, which it ignores for the PKCE verifier cookies). HttpOnly:
 * no browser JS - so no XSS - can read the tokens, and this app has no
 * browser-side Supabase client that would need to. Secure outside local
 * dev. SameSite=Lax so the OAuth redirect back still carries them. */
export const COOKIE_FLAGS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax",
} as const;
