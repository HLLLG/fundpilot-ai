import { afterEach, expect, it, vi } from "vitest";

import {
  fetchAdminUsers as facadeFetchAdminUsers,
} from "@/lib/api";
import {
  fetchAdminUsers,
} from "@/lib/api/adminUsers";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("keeps the API facade bound to the admin-user domain module", () => {
  expect(facadeFetchAdminUsers).toBe(fetchAdminUsers);
});

it("sends email search terms in a no-store request body, not the access-log URL", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ items: [], page: 1, pageSize: 20, total: 0, totalPages: 1 }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await fetchAdminUsers({ query: "private@example.com" });

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toContain("/api/admin/users/search");
  expect(url).not.toContain("private@example.com");
  expect(init.method).toBe("POST");
  expect(init.cache).toBe("no-store");
  expect(JSON.parse(String(init.body))).toMatchObject({ query: "private@example.com" });
});
