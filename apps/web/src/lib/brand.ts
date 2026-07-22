export const SITE_REGISTRATION = {
  registeredSiteName: "数据分析学习笔记",
  icpRecordNumber: "粤ICP备2026100543号-1",
  icpQueryUrl: "https://beian.miit.gov.cn/",
} as const;

export const BRAND = {
  name: SITE_REGISTRATION.registeredSiteName,
  englishName: "DATA ANALYSIS NOTES",
  productName: SITE_REGISTRATION.registeredSiteName,
  siteUrl: "https://www.hllingxi.cn",
  title: SITE_REGISTRATION.registeredSiteName,
  description:
    "数据分析学习笔记：智能识别基金持仓，追踪市场与板块变化，结合量化证据生成个性化投研分析与风险提示。",
} as const;
