import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import TopBar from "../components/TopBar";

export default function NotFound() {
  return (
    <>
      <TopBar title="Not found" />
      <div className="p-10 max-w-md mx-auto text-center">
        <div className="text-6xl font-bold text-brand-100">404</div>
        <h2 className="text-lg font-semibold text-ink-900 mt-4">Page not found</h2>
        <p className="text-sm text-ink-500 mt-1">
          The page you're looking for doesn't exist (yet).
        </p>
        <Link to="/" className="btn-primary mt-6">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to dashboard
        </Link>
      </div>
    </>
  );
}
