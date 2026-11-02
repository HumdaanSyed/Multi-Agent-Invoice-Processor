import { redirect } from "next/navigation";
import { Card, ErrorBox, primaryButton, secondaryButton } from "@/components/primitives";
import { PRODUCT_NAME } from "@/lib/config";
import { isAllowed } from "@/lib/supabase/env";
import { verifiedUserId } from "@/lib/supabase/server";

const ERRORS: Record<string, string> = {
  oauth: "Google sign-in didn't complete. Try again.",
  denied: "This Google account doesn't have access to this deployment.",
};

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { error } = await searchParams;
  const userId = await verifiedUserId();
  if (userId && isAllowed(userId)) redirect("/");
  const message = typeof error === "string" ? ERRORS[error] : undefined;

  return (
    <div className="mx-auto mt-16 max-w-md">
      <Card className="space-y-5">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Sign in to {PRODUCT_NAME.toLowerCase()}</h1>
          <p className="mt-1 text-sm text-slate-500">Access is limited to the account owner.</p>
        </div>
        {message && <ErrorBox>{message}</ErrorBox>}
        {userId ? (
          <>
            {/* Shown to the signed-in user only (their own id), so the owner
                can copy it into OWNER_USER_ID on first setup. */}
            <p className="text-sm text-slate-500">
              Signed in as user id <code className="break-all font-mono text-xs">{userId}</code>
            </p>
            <form action="/auth/signout" method="post">
              <button type="submit" className={secondaryButton}>
                Sign out
              </button>
            </form>
          </>
        ) : (
          <form action="/auth/login" method="post">
            <button type="submit" className={`${primaryButton} w-full justify-center`}>
              Continue with Google
            </button>
          </form>
        )}
      </Card>
    </div>
  );
}
