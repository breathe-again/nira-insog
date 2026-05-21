import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";

export default function Layout({ children }: { children?: ReactNode }) {
  return (
    <div className="min-h-full flex bg-ink-50">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Each page renders its own TopBar so it can set title + actions */}
        <div className="flex-1 min-w-0">{children ?? <Outlet />}</div>
      </div>
    </div>
  );
}
