import { ComingSoon } from "@/components/coming-soon";
import { PortalShell } from "@/components/portal-shell";

export default function ClaimantPortalPage() {
  return (
    <PortalShell title="Claimant portal">
      <ComingSoon portalName="Claimant" />
    </PortalShell>
  );
}
