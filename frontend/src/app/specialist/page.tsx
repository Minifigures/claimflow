import { ComingSoon } from "@/components/coming-soon";
import { PortalShell } from "@/components/portal-shell";

export default function SpecialistPortalPage() {
  return (
    <PortalShell title="Medical specialist portal">
      <ComingSoon portalName="Medical specialist" />
    </PortalShell>
  );
}
