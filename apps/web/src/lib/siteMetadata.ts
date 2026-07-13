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
        url: "/social-card.jpg",
        width: 512,
        height: 512,
        alt: BRAND.name,
      },
    ],
  },
  twitter: {
    card: "summary",
    title: BRAND.title,
    description: BRAND.description,
    images: ["/social-card.jpg"],
  },
  icons: {
    icon: [{ url: "/icon.png", type: "image/png", sizes: "64x64" }],
    apple: [{ url: "/apple-icon.png", type: "image/png", sizes: "180x180" }],
  },
};
