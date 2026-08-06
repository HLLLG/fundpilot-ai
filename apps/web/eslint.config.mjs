import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const eslintConfig = [
  // 构建产物一律不参与检查。
  //
  // eslint-config-next 只忽略 `.next`，而 next.config.ts 在开发模式下把 distDir
  // 切成 `.next-dev`（避免 dev 与 build 互相覆盖），静态导出又会产出 `out/`。
  // 这两个目录都逃过了默认忽略，于是任何跑过一次 `npm run dev` 的机器上
  // `npm run lint`（--max-warnings=0）都会因为编译产物里的上百条告警直接失败，
  // 本地等于没有可用的 lint。CI 是全新检出、目录不存在，所以此前一直没暴露。
  {
    ignores: [
      ".next/**",
      ".next-dev/**",
      "out/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  ...nextVitals,
  ...nextTypescript,
  {
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default eslintConfig;
