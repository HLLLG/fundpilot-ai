/**
 * API 基地址，从 core.ts 中单独拆出。
 *
 * 原因是循环依赖：`core.ts` 的 apiFetch 需要在请求失败时上报错误，而错误上报器
 * 又需要 API 基地址。上报器故意使用原生 fetch 而不是 apiFetch（否则"上报失败"
 * 本身会再次触发上报，形成递归），因此把这个常量放到一个无依赖的模块里，
 * 两边都从这里取。`core.ts` 仍然会 re-export `API_BASE`，既有引用无需改动。
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

/** 可选的版本标识，用于把报错关联到某次发版。未配置时为 null。 */
export const RELEASE_TAG = process.env.NEXT_PUBLIC_RELEASE ?? null;
