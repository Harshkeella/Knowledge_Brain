import { RequireAuth } from "@/components/auth/require-auth";

/**
 * Wraps the whole authenticated area -- including /dashboard/chat, which sits
 * outside the (shell) group and would otherwise be unguarded.
 */
export default function DashboardAuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <RequireAuth>{children}</RequireAuth>;
}
