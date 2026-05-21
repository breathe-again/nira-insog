import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface Props {
  Icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}

export default function EmptyState({ Icon, title, description, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      {Icon && (
        <div className="h-12 w-12 rounded-2xl bg-ink-100 text-ink-500 flex items-center justify-center mb-3">
          <Icon className="h-5 w-5" />
        </div>
      )}
      <h4 className="text-sm font-semibold text-ink-900">{title}</h4>
      {description && (
        <p className="text-sm text-ink-500 mt-1 max-w-sm">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
