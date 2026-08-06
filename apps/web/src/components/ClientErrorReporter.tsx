"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import {
  addClientBreadcrumb,
  installClientErrorReporter,
} from "@/lib/clientErrorReporter";

/**
 * 挂载全局错误监听，并把路由跳转记为操作痕迹。
 *
 * 放在 root layout 里，这样即使用户还没登录（登录页自身崩溃）也在覆盖范围内。
 * 组件本身不渲染任何内容。
 */
export function ClientErrorReporter() {
  const pathname = usePathname();

  useEffect(() => installClientErrorReporter(), []);

  useEffect(() => {
    if (pathname) {
      // 崩溃报告里会带上最近若干跳，用来还原"用户是怎么走到这一步的"。
      addClientBreadcrumb(`route:${pathname}`);
    }
  }, [pathname]);

  return null;
}
