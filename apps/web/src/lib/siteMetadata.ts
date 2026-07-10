import type { Metadata } from "next";

import { BRAND } from "@/lib/brand";


export const SITE_METADATA: Metadata = {
  metadataBase: new URL(BRAND.siteUrl),
  title: BRAND.title,
  description: BRAND.description,
  applicationName: BRAND.productName,
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    locale: "zh_CN",
    url: "/",
    siteName: BRAND.productName,
    title: BRAND.title,
    description: BRAND.description,
    images: [
      {
        url: "/icon.png",
        width: 512,
        height: 512,
        alt: BRAND.name,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: BRAND.title,
    description: BRAND.description,
    images: ["/icon.png"],
  },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon.png", type: "image/png" },
    ],
    apple: [{ url: "/icon.png", type: "image/png" }],
  },
};
