"use client";

import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { getStorageUsage, type StorageUsage } from "@/lib/api";

function gb(bytes: number): string {
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Storage used and a way out.
 *
 * The bar is a read of the server's own accounting, not a client-side tally
 * of what was uploaded this session -- so it stays right across a refresh,
 * a second tab, and an ingest that failed halfway.
 */
export function AccountMenu() {
  const { email, enabled, signOut } = useAuth();
  const [usage, setUsage] = useState<StorageUsage | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStorageUsage()
      .then((value) => !cancelled && setUsage(value))
      // A usage read is decoration; failing it must not break the nav.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const percent = usage
    ? Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100)
    : 0;

  return (
    <div className="ml-auto flex items-center gap-4">
      {usage ? (
        <div className="hidden w-40 sm:block" title={`${usage.document_count} documents`}>
          <div className="mb-1 flex justify-between text-xs text-muted-foreground">
            <span>Storage</span>
            <span>
              {gb(usage.used_bytes)} / {gb(usage.quota_bytes)}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={Math.round(percent)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Storage used"
            className="h-1 w-full overflow-hidden rounded-full bg-muted"
          >
            <div
              className="h-full rounded-full bg-foreground transition-[width]"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      ) : null}

      {enabled ? (
        <button
          type="button"
          onClick={signOut}
          title={email ?? "Sign out"}
          className="flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <LogOut className="size-4" />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      ) : null}
    </div>
  );
}
