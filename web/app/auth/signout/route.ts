import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { siteUrl } from "@/lib/site-url";

/** POST only (a GET link could be triggered cross-site); clears the cookies
 * and revokes the refresh token server-side. */
export async function POST(request: NextRequest) {
  const supabase = await createClient();
  await supabase.auth.signOut();
  return NextResponse.redirect(`${siteUrl(request)}/login`, { status: 303 });
}
