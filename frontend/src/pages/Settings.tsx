import { Building2, KeyRound, Mail, ShieldCheck, Users } from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";

export default function Settings() {
  return (
    <>
      <TopBar
        title="Settings"
        subtitle="Workspace · team · integrations (placeholders)"
      />

      <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard
          title="Workspace"
          subtitle="Organization details"
        >
          <ul className="space-y-3">
            <SettingRow Icon={Building2} label="Organization name" value="Demo Org" />
            <SettingRow Icon={Mail} label="Email-to-inbox" value="demo@in.nira-insig.com" />
            <SettingRow Icon={ShieldCheck} label="Plan" value="Trial" />
          </ul>
        </SectionCard>

        <SectionCard title="Team" subtitle="Members and roles">
          <ul className="space-y-3">
            <SettingRow Icon={Users} label="Founder" value="founder@demo.local" />
          </ul>
          <button className="btn-primary mt-4">Invite teammate</button>
          <div className="text-xs text-ink-500 mt-2">
            Auth + invites land in the next sprint.
          </div>
        </SectionCard>

        <SectionCard
          title="Integrations"
          subtitle="Sources we'll pull from once enabled"
          className="lg:col-span-2"
        >
          <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <IntegrationCard name="Gmail (email-to-inbox)" status="planned" />
            <IntegrationCard name="WhatsApp Business" status="planned" />
            <IntegrationCard name="Account Aggregator (banks)" status="planned" />
            <IntegrationCard name="Tally / Zoho Books" status="planned" />
            <IntegrationCard name="GST portal" status="planned" />
            <IntegrationCard name="Slack alerts" status="planned" />
          </ul>
        </SectionCard>

        <SectionCard title="Security" subtitle="Account safety" className="lg:col-span-2">
          <ul className="space-y-3">
            <SettingRow Icon={KeyRound} label="Two-factor auth" value="Not configured" />
            <SettingRow Icon={ShieldCheck} label="Session policy" value="Default" />
          </ul>
        </SectionCard>
      </div>
    </>
  );
}

function SettingRow({
  Icon,
  label,
  value,
}: {
  Icon: typeof Building2;
  label: string;
  value: string;
}) {
  return (
    <li className="flex items-center gap-3">
      <div className="h-9 w-9 rounded-lg bg-ink-100 text-ink-600 flex items-center justify-center">
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1">
        <div className="text-xs text-ink-500 uppercase tracking-wide">{label}</div>
        <div className="text-sm text-ink-900">{value}</div>
      </div>
    </li>
  );
}

function IntegrationCard({ name, status }: { name: string; status: "planned" | "live" }) {
  const live = status === "live";
  return (
    <li className="rounded-xl ring-1 ring-ink-200 bg-white p-4 flex items-center justify-between">
      <span className="text-sm font-medium text-ink-900">{name}</span>
      <span
        className={
          "chip ring-1 " +
          (live
            ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
            : "bg-ink-50 text-ink-500 ring-ink-200")
        }
      >
        {live ? "Live" : "Planned"}
      </span>
    </li>
  );
}
