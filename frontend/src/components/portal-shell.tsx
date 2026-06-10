import type { ReactNode } from "react";

import { LogoutButton } from "@/components/logout-button";

interface PortalShellProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
}

export function PortalShell({ title, subtitle, children }: PortalShellProps) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-blue-700">
              ClaimFlow
            </p>
            <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
            {subtitle ? <p className="text-sm text-slate-500">{subtitle}</p> : null}
          </div>
          <LogoutButton />
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  );
}
