import { Bell, Search, UserRound } from "lucide-react";

interface Props {
  /** The page title shown on the left of the top bar. */
  title: string;
  /** Optional subtitle / breadcrumb-y text. */
  subtitle?: string;
  /** Right-aligned action(s). */
  actions?: React.ReactNode;
}

export default function TopBar({ title, subtitle, actions }: Props) {
  return (
    <header className="h-14 px-6 bg-white ring-1 ring-ink-200 flex items-center justify-between">
      <div>
        <h1 className="text-base font-semibold text-ink-900 leading-tight">{title}</h1>
        {subtitle && <div className="text-xs text-ink-500">{subtitle}</div>}
      </div>

      <div className="flex items-center gap-2">
        <div className="hidden md:flex items-center gap-2 px-3 h-9 rounded-lg bg-ink-50 ring-1 ring-ink-200 w-72 text-sm text-ink-500">
          <Search className="h-4 w-4" />
          <input
            placeholder="Search…"
            className="bg-transparent outline-none flex-1 placeholder:text-ink-400 text-ink-800"
          />
          <kbd className="hidden lg:inline text-[10px] px-1 py-0.5 rounded bg-white ring-1 ring-ink-200 text-ink-500 font-mono">
            ⌘K
          </kbd>
        </div>

        {actions}

        <button
          className="h-9 w-9 rounded-lg flex items-center justify-center text-ink-600 hover:bg-ink-100 transition-colors"
          aria-label="Notifications"
        >
          <Bell className="h-4 w-4" />
        </button>

        <div className="flex items-center gap-2 pl-2 ml-1 border-l border-ink-200 h-9">
          <div className="h-8 w-8 rounded-full bg-gradient-to-br from-brand-400 to-brand-700 text-white flex items-center justify-center">
            <UserRound className="h-4 w-4" />
          </div>
          <div className="leading-tight hidden sm:block">
            <div className="text-xs font-semibold text-ink-900">Founder</div>
            <div className="text-[10px] text-ink-500">Demo Org</div>
          </div>
        </div>
      </div>
    </header>
  );
}
