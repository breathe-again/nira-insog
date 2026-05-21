/** Authentication context.
 *
 * Holds the current user (or `null` if anonymous) and exposes `signup`,
 * `login`, `logout`. On mount, hits /api/auth/me to bootstrap from the
 * httpOnly cookie (if one exists).
 *
 * The provider also listens for `NotAuthenticatedError` from the API client
 * and clears the user — that's how a hard 401 (refresh failed) flushes us
 * back to the login page.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, NotAuthenticatedError } from "../api";
import type { AuthMeOut } from "../types";

interface AuthState {
  user: AuthMeOut | null;
  loading: boolean;
}

interface AuthContextValue extends AuthState {
  signup: (org_name: string, email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null, loading: true });
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      if (mountedRef.current) setState({ user: me, loading: false });
    } catch (e) {
      if (e instanceof NotAuthenticatedError) {
        if (mountedRef.current) setState({ user: null, loading: false });
      } else {
        // Treat transient errors as unauth too — better than spinning forever.
        if (mountedRef.current) setState({ user: null, loading: false });
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signup = useCallback(
    async (org_name: string, email: string, password: string) => {
      const tokens = await api.signup(org_name, email, password);
      setState({ user: tokens.user, loading: false });
    },
    [],
  );

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api.login(email, password);
    setState({ user: tokens.user, loading: false });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // swallow — we clear the local state regardless
    }
    setState({ user: null, loading: false });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, signup, login, logout, refresh }),
    [state, signup, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
