/** Route guard. Renders the children only if a user is authenticated.
 *
 * On boot we're still loading (waiting for /api/auth/me) — show a small
 * spinner instead of flashing the login page.
 */

import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

export default function ProtectedRoute() {
  const { user, loading } = useAuth();
  const loc = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-ink-50">
        <Loader2 className="h-5 w-5 animate-spin text-ink-400" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  }

  return <Outlet />;
}
