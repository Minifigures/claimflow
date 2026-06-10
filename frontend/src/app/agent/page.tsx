import { ComingSoon } from "@/components/coming-soon";
import { PortalShell } from "@/components/portal-shell";

export default function AgentPortalPage() {
  return (
    <PortalShell title="Insurance agent portal">
      <ComingSoon portalName="Insurance agent" />
    </PortalShell>
  );
}
