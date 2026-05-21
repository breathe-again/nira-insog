import { NavLink } from "react-router-dom";
import {
  Activity,
  Inbox,
  LayoutDashboard,
  type LucideIcon,
  Settings,
  Sparkles,
} from "lucide-react";
import { cn } from "../lib/cn";

interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  badge?: string;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Workspace",
    items: [
      { to: "/", label: "Dashboard", Icon: LayoutDashboard },
      { to: "/inbox", label: "Inbox", Icon: Inbox },
    ],
  },
  {
    section: "Admin",
    items: [
      { to: "/system", label: "System", Icon: Activity },
      { to: "/settings", label: "Settings", Icon: Settings },
    ],
  },
];

export default function Sidebar() {
  return (
    <aside className="w-60 shrink-0 bg-white ring-1 ring-ink-200 flex flex-col">
      <div className="h-14 px-4 flex items-center gap-2 border-b border-ink-100">
        <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-white font-bold flex items-center justify-center shadow-sm">
          N
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-ink-900">Nira Insig</div>
          <div className="text-[10px] uppercase tracking-wider text-ink-500">
            Finance · v0
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto p-3 space-y-5">
        {NAV.map((group) => (
          <div key={group.section}>
            <div className="px-2 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-500">
              {group.section}
            </div>
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm font-medium transition-colors",
                        isActive
                          ? "bg-brand-50 text-brand-700"
                          : "text-ink-700 hover:bg-ink-100 hover:text-ink-900",
                      )
                    }
                  >
                    <item.Icon className="h-4 w-4" />
                    <span>{item.label}</span>
                    {item.badge && (
                      <span className="ml-auto chip bg-brand-100 text-brand-700">
                        {item.badge}
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="p-3 border-t border-ink-100">
        <div className="card-hover rounded-xl bg-gradient-to-br from-brand-50 to-violet-50 ring-1 ring-brand-100 p-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-brand-800">
            <Sparkles className="h-3.5 w-3.5" />
            Demo workspace
          </div>
          <p className="text-[11px] text-ink-600 mt-1 leading-snug">
            Auth is stubbed in v0. All data belongs to <span className="font-medium">Demo Org</span>.
          </p>
        </div>
      </div>
    </aside>
  );
}
