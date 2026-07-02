const TOKEN_KEY = "fundpilot_access_token";

export type AuthUser = {
  id: number;
  userRole: string;
  username: string;
  userAccount: string;
  bio: string;
  avatarUrl: string;
};

export type AuthSession = {
  accessToken: string;
  expiresIn: number;
  user: AuthUser;
};

export function getAccessToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return localStorage.getItem(TOKEN_KEY);
}

export function saveAccessToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}
