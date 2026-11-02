import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { siteUrl } from "@/lib/site-url";

/** Google -> Supabase -> here with ?code=. Exchanging it sets the session
 * cookies. Always lands on "/" - no caller-supplied redirect target, so
 * there is no open redirect to abuse. */
export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${siteUrl(request)}/`);
  }
  return NextResponse.redirect(`${siteUrl(request)}/login?error=oauth`);
}
