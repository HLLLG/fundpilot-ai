"use client";

import { Dashboard } from "@/components/Dashboard";
import { LandingPage } from "@/components/LandingPage";
import { useAuth } from "@/components/AuthProvider";

export default function Home() {
  const { user } = useAuth();
  return user ? <Dashboard /> : <LandingPage />;
}
