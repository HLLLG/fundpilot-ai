import { HomeClient } from "@/components/HomeClient";
import { LandingPage } from "@/components/LandingPage";

export default function Home() {
  return <HomeClient landing={<LandingPage />} />;
}
