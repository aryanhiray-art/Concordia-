"use client";

// /join is the sign-in and invite-redemption route. It renders nothing of its
// own: AppShell substitutes <AuthGate/> for the page whenever nobody is signed
// in, which is the only state this route exists to serve. The body below is
// therefore just the already-signed-in case — bounce them into the app rather
// than showing an empty shell.

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { LoadingScreen } from "@/components/ui";

export default function JoinPage() {
  const { member } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (member) router.replace("/");
  }, [member, router]);

  return <LoadingScreen label="loading" />;
}
