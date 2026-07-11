# Daily Report Chat Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let desktop users scroll and inspect the daily report while a sticky chat companion remains open, without changing the mobile/tablet modal behavior or duplicating chat/SSE state.

**Architecture:** Add a reactive `matchMedia` hook, render one `ReportChatPanel` subtree, and switch only the outer layer’s layout and accessibility semantics at 1280px. `ReportPanel` becomes a report workspace containing sibling document and chat surfaces; CSS turns that workspace into a two-column grid only while the desktop sidecar is mounted. The report summary responds to its container width instead of the viewport.

**Tech Stack:** React 19, TypeScript, `useSyncExternalStore`, Tailwind utilities, plain CSS container queries, Vitest, Testing Library.

---

## File map

- Create `apps/web/src/lib/useMediaQuery.ts`: SSR-safe reactive media query hook.
- Create `apps/web/src/test/matchMedia.ts`: deterministic Vitest matchMedia controller.
- Create `apps/web/src/lib/useMediaQuery.test.tsx`: hook subscription contract.
- Modify `apps/web/src/components/ReportChatDrawer.tsx`: desktop complementary sidecar and mobile modal effects.
- Modify `apps/web/src/components/ReportChatDrawer.test.tsx`: mode-specific semantics and focus tests.
- Modify `apps/web/src/components/ReportPanel.tsx`: sibling report workspace.
- Modify `apps/web/src/components/ReportPanel.test.tsx`: workspace structure tests.
- Modify `apps/web/src/components/ReportSummaryHero.tsx`: container-responsive class.
- Modify `apps/web/src/components/ReportSummaryHero.test.tsx`: no viewport-only breakpoint regression.
- Modify `apps/web/src/components/ReportChatPanel.test.tsx`: one history/SSE instance across breakpoint changes and abort-on-close.
- Modify `apps/web/src/app/globals.css`: grid, sidecar, modal, animation, and container-query rules.

### Task 1: Add an SSR-safe reactive media query hook

**Files:**
- Create: `apps/web/src/lib/useMediaQuery.ts`
- Create: `apps/web/src/test/matchMedia.ts`
- Create: `apps/web/src/lib/useMediaQuery.test.tsx`

- [ ] **Step 1: Write the failing hook test and matchMedia controller**

Create `apps/web/src/test/matchMedia.ts`:

```ts
import { vi } from "vitest";

type Listener = (event: MediaQueryListEvent) => void;

export function installMatchMedia(initialMatches = false) {
  let matches = initialMatches;
  const listeners = new Set<Listener>();
  const media = vi.fn((query: string): MediaQueryList => ({
    media: query,
    get matches() {
      return matches;
    },
    onchange: null,
    addEventListener: (_type, listener) => listeners.add(listener as Listener),
    removeEventListener: (_type, listener) => listeners.delete(listener as Listener),
    addListener: (listener) => listeners.add(listener as Listener),
    removeListener: (listener) => listeners.delete(listener as Listener),
    dispatchEvent: () => true,
  }));
  vi.stubGlobal("matchMedia", media);
  return {
    media,
    setMatches(next: boolean) {
      matches = next;
      const event = { matches, media: media.mock.calls[0]?.[0] ?? "" } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
}
```

Create `apps/web/src/lib/useMediaQuery.test.tsx`:

```tsx
// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { installMatchMedia } from "@/test/matchMedia";
import { useMediaQuery } from "@/lib/useMediaQuery";

function Probe() {
  const matches = useMediaQuery("(min-width: 1280px)");
  return <output>{matches ? "desktop" : "compact"}</output>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("tracks matchMedia change events", () => {
  const controller = installMatchMedia(false);
  render(<Probe />);
  expect(screen.getByText("compact")).toBeTruthy();

  act(() => controller.setMatches(true));

  expect(screen.getByText("desktop")).toBeTruthy();
});
```

- [ ] **Step 2: Run the test and confirm failure**

```powershell
cd apps/web
npm test -- src/lib/useMediaQuery.test.tsx
```

Expected: FAIL because `useMediaQuery.ts` does not exist.

- [ ] **Step 3: Implement the hook with `useSyncExternalStore`**

Create `apps/web/src/lib/useMediaQuery.ts`:

```ts
"use client";

import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
        return () => undefined;
      }
      const media = window.matchMedia(query);
      media.addEventListener("change", notify);
      return () => media.removeEventListener("change", notify);
    },
    [query],
  );
  const getSnapshot = useCallback(
    () =>
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia(query).matches
        : false,
    [query],
  );
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
```

- [ ] **Step 4: Run the test and commit**

```powershell
npm test -- src/lib/useMediaQuery.test.tsx
git add src/lib/useMediaQuery.ts src/lib/useMediaQuery.test.tsx src/test/matchMedia.ts
git commit -m "feat: add reactive media query hook"
```

Expected: PASS.

### Task 2: Split desktop sidecar effects from compact modal effects

**Files:**
- Modify: `apps/web/src/components/ReportChatDrawer.tsx:1-106`
- Modify: `apps/web/src/components/ReportChatDrawer.test.tsx`

- [ ] **Step 1: Replace the drawer tests with explicit mode contracts**

Keep the current `ReportChatPanel` mock. Import `installMatchMedia`; every test calls it before render, while `afterEach` calls `vi.unstubAllGlobals()` in addition to the existing cleanup. Add these tests:

```tsx
it("keeps phone and tablet chat modal", () => {
  installMatchMedia(false);
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "追问这份日报" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(document.body.style.overflow).toBe("hidden");
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(trigger).toHaveAttribute("tabindex", "-1");
});

it("opens a non-modal desktop sidecar", () => {
  installMatchMedia(true);
  document.body.style.overflow = "clip";
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));

  const sidecar = screen.getByRole("complementary", { name: "追问这份日报" });
  expect(sidecar).not.toHaveAttribute("aria-modal");
  expect(document.body.style.overflow).toBe("clip");
  const event = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
  document.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
});

it.each([
  [false, "dialog"],
  [true, "complementary"],
] as const)("closes %s mode on Escape and restores trigger focus", (desktop, role) => {
  installMatchMedia(desktop);
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);
  expect(screen.getByRole(role)).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole(role)).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("only closes from backdrop in compact mode", () => {
  installMatchMedia(false);
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  fireEvent.mouseDown(screen.getByTestId("report-chat-layer"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
```

Delete the legacy `compact` migration test; it does not describe a production caller.

- [ ] **Step 2: Run the drawer tests and confirm failure**

```powershell
npm test -- src/components/ReportChatDrawer.test.tsx
```

Expected: desktop tests fail because all viewports are currently modal and the trigger lacks ARIA state.

- [ ] **Step 3: Implement one subtree with two behavior modes**

In `ReportChatDrawer.tsx`, import `useMediaQuery`, define:

```ts
const DESKTOP_REPORT_QUERY = "(min-width: 1280px)";
const CHAT_PANEL_ID = "report-chat-drawer-panel";
```

Inside the component:

```ts
const isDesktop = useMediaQuery(DESKTOP_REPORT_QUERY);
```

Split effects exactly as follows:

```ts
useEffect(() => {
  if (!open) return;
  const trigger = triggerRef.current;
  closeRef.current?.focus();
  const onEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") setOpen(false);
  };
  document.addEventListener("keydown", onEscape);
  return () => {
    document.removeEventListener("keydown", onEscape);
    trigger?.focus();
  };
}, [open]);

useEffect(() => {
  if (!open || isDesktop) return;
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  const onTab = (event: KeyboardEvent) => {
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onTab);
  return () => {
    document.removeEventListener("keydown", onTab);
    document.body.style.overflow = previousOverflow;
  };
}, [isDesktop, open]);
```

Give the trigger `aria-expanded={open}`, `aria-controls={CHAT_PANEL_ID}`, `tabIndex={open ? -1 : 0}`, and append `pointer-events-none invisible` while open.

Use a single conditional subtree:

```tsx
{open ? (
  <div
    data-testid="report-chat-layer"
    className="report-chat-layer fixed inset-0 z-50 flex items-end justify-end bg-slate-950/35 backdrop-blur-[2px] sm:items-stretch"
    onMouseDown={(event) => {
      if (!isDesktop && event.target === event.currentTarget) setOpen(false);
    }}
  >
    <section
      id={CHAT_PANEL_ID}
      ref={dialogRef}
      role={isDesktop ? "complementary" : "dialog"}
      aria-modal={isDesktop ? undefined : true}
      aria-labelledby="report-chat-drawer-title"
      className="report-chat-drawer flex h-[min(82dvh,720px)] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl shadow-slate-950/20 sm:h-[100dvh] sm:w-[420px] sm:rounded-none"
    >
      <header className="flex min-h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
        <h2 id="report-chat-drawer-title" className="text-base font-black text-slate-950">
          追问这份日报
        </h2>
        <button
          ref={closeRef}
          type="button"
          onClick={() => setOpen(false)}
          aria-label="关闭追问助手"
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-500 transition hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        >
          <X aria-hidden="true" size={20} />
        </button>
      </header>
      <div className="min-h-0 flex-1 pb-[env(safe-area-inset-bottom)] sm:pb-0">
        <ReportChatPanel reportId={reportId} reportTitle={reportTitle} variant="drawer" />
      </div>
    </section>
  </div>
) : null}
```

The existing header and `ReportChatPanel` JSX must be moved intact into the section; do not create desktop/mobile copies.

- [ ] **Step 4: Run the drawer tests and commit**

```powershell
npm test -- src/components/ReportChatDrawer.test.tsx
npm run typecheck
git add src/components/ReportChatDrawer.tsx src/components/ReportChatDrawer.test.tsx
git commit -m "feat: add desktop report chat sidecar"
```

Expected: PASS.

### Task 3: Put report and sidecar in one workspace and preserve one chat instance

**Files:**
- Modify: `apps/web/src/components/ReportPanel.tsx:95-111`
- Modify: `apps/web/src/components/ReportPanel.test.tsx`
- Modify: `apps/web/src/components/ReportChatPanel.test.tsx`
- Modify: `apps/web/src/app/globals.css:877-921`

- [ ] **Step 1: Add failing workspace and breakpoint-lifecycle tests**

In `ReportPanel.test.tsx`, add:

```tsx
it("places report and sidecar in one workspace", () => {
  installMatchMedia(true);
  render(<ReportPanel report={sampleReport()} streaming={null} />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));

  const workspace = screen.getByTestId("report-workspace");
  const report = screen.getByTestId("report-ready");
  const sidecar = screen.getByRole("complementary", { name: "追问这份日报" });
  expect(workspace).toContainElement(report);
  expect(workspace).toContainElement(sidecar);
  expect(report).not.toContainElement(sidecar);
  expect(screen.getByRole("button", { name: "追问这份日报" }).closest(".animate-fade-up"))
    .toBeNull();
});
```

In `ReportChatPanel.test.tsx`, import `act` and `installMatchMedia`, then add:

```tsx
it("keeps one chat instance and active stream across breakpoint changes", async () => {
  const media = installMatchMedia(false);
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  await waitFor(() => expect(apiMocks.fetchReportChatHistory).toHaveBeenCalledTimes(1));
  await sendQuestion("断点切换中的问题");
  const signal = apiMocks.streamReportChat.mock.calls[0]?.[4] as AbortSignal;

  act(() => media.setMatches(true));
  expect(screen.getByRole("complementary", { name: "追问这份日报" })).toBeInTheDocument();
  expect(signal.aborted).toBe(false);

  act(() => media.setMatches(false));
  expect(screen.getByRole("dialog", { name: "追问这份日报" })).toBeInTheDocument();
  expect(apiMocks.fetchReportChatHistory).toHaveBeenCalledTimes(1);
  expect(signal.aborted).toBe(false);
});

it.each([false, true])("aborts the active stream when %s mode closes", async (desktop) => {
  installMatchMedia(desktop);
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  await sendQuestion("关闭前的问题");
  const signal = apiMocks.streamReportChat.mock.calls[0]?.[4] as AbortSignal;

  fireEvent.click(screen.getByRole("button", { name: "关闭追问助手" }));

  expect(signal.aborted).toBe(true);
});
```

Call `vi.unstubAllGlobals()` in this file’s `afterEach`.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
npm test -- src/components/ReportPanel.test.tsx src/components/ReportChatPanel.test.tsx
```

Expected: workspace structure fails and breakpoint lifecycle coverage fails until the wrapper/CSS are implemented.

- [ ] **Step 3: Implement the workspace and responsive CSS**

Change `ReportPanel.tsx` to:

```tsx
return (
  <div className="report-workspace" data-testid="report-workspace">
    <section
      className="report-shell min-w-0 space-y-4 animate-fade-up"
      data-testid="report-ready"
    >
      <ReportSummaryHero
        report={report}
        needsActionCount={groups.needsAction.length}
        isExporting={isExporting}
        onExport={() => void handleExportMarkdown()}
      />
      <ReportRecommendationList report={report} recommendations={fundRecommendations} />
      <ReportDetailsHub report={report} diagnostics={diagnostics} />
    </section>
    <ReportChatDrawer reportId={report.id} reportTitle={report.title} />
  </div>
);
```

Replace the report chat CSS block in `globals.css` with:

```css
.report-workspace {
  min-width: 0;
  width: 100%;
}

.report-shell {
  width: 100%;
  container-name: report-content;
  container-type: inline-size;
}

.report-chat-layer {
  animation: report-drawer-fade 160ms ease-out both;
}

.report-chat-drawer {
  animation: report-drawer-in 220ms cubic-bezier(0.22, 0.61, 0.36, 1) both;
}

@media (min-width: 1280px) {
  .report-workspace:has(.report-chat-drawer) {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(360px, 400px);
    align-items: start;
    gap: 1.5rem;
  }

  .report-chat-layer {
    position: static;
    inset: auto;
    z-index: auto;
    display: contents;
    background: transparent;
    backdrop-filter: none;
    animation: none;
  }

  .report-chat-drawer {
    position: sticky;
    top: 4.5rem;
    width: 100%;
    height: min(calc(100dvh - 5.5rem), 60rem);
    border: 1px solid var(--line);
    border-radius: 1.25rem;
    box-shadow: 0 18px 48px rgba(15, 23, 42, 0.12);
  }
}
```

Update reduced-motion selectors from `.report-chat-backdrop` to `.report-chat-layer`.

- [ ] **Step 4: Run tests and commit**

```powershell
npm test -- src/components/ReportPanel.test.tsx src/components/ReportChatPanel.test.tsx src/components/ReportChatDrawer.test.tsx
git add src/components/ReportPanel.tsx src/components/ReportPanel.test.tsx src/components/ReportChatPanel.test.tsx src/app/globals.css
git commit -m "feat: add report reading workspace"
```

Expected: PASS and one chat instance survives breakpoint changes.

### Task 4: Make the summary respond to report-column width

**Files:**
- Modify: `apps/web/src/components/ReportSummaryHero.tsx:68-99`
- Modify: `apps/web/src/components/ReportSummaryHero.test.tsx`
- Modify: `apps/web/src/app/globals.css`

- [ ] **Step 1: Add the failing class contract**

Append to `ReportSummaryHero.test.tsx`:

```tsx
it("uses the report container instead of a viewport-only grid breakpoint", () => {
  const { container } = render(
    <ReportSummaryHero
      report={sampleReport()}
      needsActionCount={1}
      isExporting={false}
      onExport={vi.fn()}
    />,
  );
  const layout = container.querySelector(".report-summary-layout");
  expect(layout).not.toBeNull();
  expect(layout?.className).not.toContain("lg:grid-cols");
});
```

- [ ] **Step 2: Run and confirm failure**

```powershell
npm test -- src/components/ReportSummaryHero.test.tsx
```

Expected: FAIL because the layout still uses `lg:grid-cols-*`.

- [ ] **Step 3: Implement container-driven summary layout**

Change the layout div to:

```tsx
<div className="report-summary-layout grid min-w-0 gap-5">
```

Add to `globals.css`:

```css
@container report-content (min-width: 48rem) {
  .report-summary-layout {
    grid-template-columns: minmax(0, 1fr) minmax(18rem, 25rem);
    align-items: start;
  }
}
```

- [ ] **Step 4: Run tests, build, and commit**

```powershell
npm test -- src/components/ReportSummaryHero.test.tsx src/components/ReportPanel.test.tsx
npm run typecheck
npm run lint
npm run build
git add src/components/ReportSummaryHero.tsx src/components/ReportSummaryHero.test.tsx src/app/globals.css
git commit -m "fix: make report summary container responsive"
```

Expected: all commands PASS.

### Task 5: Run the complete web regression suite

**Files:**
- No production files changed in this task.

- [ ] **Step 1: Run all component and unit tests**

```powershell
cd apps/web
npm test
```

Expected: PASS.

- [ ] **Step 2: Run static and production checks**

```powershell
npm run typecheck
npm run lint
npm run build
```

Expected: PASS with no new warnings.

- [ ] **Step 3: Verify patch hygiene**

```powershell
cd ..\..
git diff --check
git status --short
```

Expected: no whitespace errors; only files assigned to this plan remain changed.
