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
      <header className="border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <p className="font-display text-sm font-semibold italic tracking-tight text-blue-800">
              ClaimFlow
            </p>
            <h1 className="font-display text-xl font-medium text-slate-900">{title}</h1>
            {subtitle ? <p className="font-mono text-xs text-slate-500">{subtitle}</p> : null}
          </div>
          <LogoutButton />
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  );
}
