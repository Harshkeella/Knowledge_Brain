import Image from "next/image";
import Link from "next/link";

import { AccountMenu } from "@/components/auth/account-menu";

const NAV_ITEMS = [
  { href: "/dashboard/knowledge", label: "Knowledge Base" },
  { href: "/dashboard/chat", label: "Chat" },
  { href: "/dashboard/graph", label: "Graph" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <nav className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-4">
          <Link
            href="/dashboard/knowledge"
            className="flex items-center gap-2 font-semibold"
          >
            <Image
              src="/logo.png"
              alt=""
              width={28}
              height={28}
              priority
              className="rounded-md"
            />
            nodeRels
          </Link>
          <div className="flex gap-4 text-sm text-muted-foreground">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
          </div>
          <AccountMenu />
        </nav>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        {children}
      </main>
    </div>
  );
}
