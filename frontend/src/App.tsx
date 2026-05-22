import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./contexts/AuthContext";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import DocumentDetail from "./pages/DocumentDetail";
import Ask from "./pages/Ask";
import Insights from "./pages/Insights";
import Learning from "./pages/Learning";
import Search from "./pages/Search";
import Duplicates from "./pages/Duplicates";
import Health from "./pages/Health";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public auth routes */}
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />

          {/* Everything else requires auth */}
          <Route element={<ProtectedRoute />}>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="dashboard" element={<Navigate to="/" replace />} />
              <Route path="inbox" element={<Inbox />} />
              <Route path="inbox/:id" element={<DocumentDetail />} />
              <Route path="ask" element={<Ask />} />
              <Route path="insights" element={<Insights />} />
              <Route path="learning" element={<Learning />} />
              <Route path="search" element={<Search />} />
              <Route path="duplicates" element={<Duplicates />} />
              <Route path="system" element={<Health />} />
              <Route path="settings" element={<Settings />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
