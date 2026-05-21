import type { ReactNode } from "react";
import { cn } from "../lib/cn";

interface Props {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export default function SectionCard({
  title,
  subtitle,
  action,
  children,
  className,
}: Props) {
  return (
    <section className={cn("card overflow-hidden", className)}>
      <header className="flex items-start justify-between px-5 pt-5 pb-3">
        <div>
          <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
          {subtitle && <p className="text-xs text-ink-500 mt-0.5">{subtitle}</p>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <div className="px-5 pb-5">{children}</div>
    </section>
  );
}
