import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { siteUrl } from "@/lib/site-url";

/** POST from the login page: starts Google OAuth (PKCE) server-side. The
 * code verifier lands in an HttpOnly cookie; no Supabase client ever runs
 * in the browser. */
export async function POST(request: NextRequest) {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: `${siteUrl(request)}/auth/callback` },
  });
  if (error || !data.url) {
    return NextResponse.redirect(`${siteUrl(request)}/login?error=oauth`, { status: 303 });
  }
  return NextResponse.redirect(data.url, { status: 303 });
}
