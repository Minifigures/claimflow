interface ComingSoonProps {
  portalName: string;
}

export function ComingSoon({ portalName }: ComingSoonProps) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
      <p className="text-base font-medium text-slate-700">{portalName} portal coming soon.</p>
      <p className="mt-1 text-sm text-slate-500">
        This area will be available in an upcoming release.
      </p>
    </div>
  );
}
