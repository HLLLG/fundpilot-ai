// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { useMediaQuery } from "@/lib/useMediaQuery";
import { installMatchMedia, type MatchMediaController } from "@/test/matchMedia";

const QUERY = "(min-width: 1280px)";

let matchMedia: MatchMediaController;

function MediaQueryProbe() {
  const matches = useMediaQuery(QUERY);
  return <output>{matches ? "desktop" : "compact"}</output>;
}

beforeEach(() => {
  matchMedia = installMatchMedia({ [QUERY]: false });
});

afterEach(() => {
  cleanup();
  matchMedia.restore();
});

it("reacts to matchMedia change events", () => {
  render(<MediaQueryProbe />);
  expect(screen.getByText("compact")).toBeInTheDocument();

  act(() => matchMedia.setMatches(QUERY, true));
  expect(screen.getByText("desktop")).toBeInTheDocument();

  act(() => matchMedia.setMatches(QUERY, false));
  expect(screen.getByText("compact")).toBeInTheDocument();
});

it("uses a deterministic compact snapshot during server rendering", () => {
  matchMedia.setMatches(QUERY, true);

  expect(renderToString(<MediaQueryProbe />)).toContain("compact");
});
