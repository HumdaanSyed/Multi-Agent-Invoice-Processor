import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { COOKIE_FLAGS, supabaseKey, supabaseUrl } from "./env";

/**
 * Server-only Supabase client bound to this request's cookies. The session
 * lives in HttpOnly cookies set by @supabase/ssr; nothing auth-related is
 * ever handed to browser JavaScript or localStorage. Only the publishable
 * key is used here - no service-role/secret key exists in the frontend.
 *
 * Env is read per request (not at build), so one image serves every deploy.
 */
export async function createClient() {
  const cookieStore = await cookies();
  return createServerClient(supabaseUrl(), supabaseKey(), {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (toSet) => {
        try {
          toSet.forEach(({ name, value, options }) => cookieStore.set(name, value, { ...options, ...COOKIE_FLAGS }));
        } catch {
          // Server Components can't set cookies; proxy.ts refreshes the session instead.
        }
      },
    },
  });
}

/** Verified user id for this request, or null. getClaims() checks the
 * access token's signature against the project's JWKS (asymmetric keys),
 * unlike getSession(), which only reads the cookie. */
export async function verifiedUserId(): Promise<string | null> {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();
  return error || !data?.claims?.sub ? null : data.claims.sub;
}
