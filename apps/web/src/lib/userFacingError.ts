"use client";

/**
 * 把任意异常收敛成"可以给用户看的一句话"。
 *
 * 背景：各处 catch 里普遍写 `error instanceof Error ? error.message : fallback`。
 * 服务端返回的业务错误（ApiError）文案是我们自己审过的中文，直接展示没问题；
 * 但一旦是前端自身的编程错误（响应结构与预期不符时对 undefined 调 `.map()`
 * 之类），这个写法会把 `Cannot read properties of undefined (reading 'map')`
 * 原样贴到用户界面上。对一个金融决策应用，这既不可行动，也泄漏实现细节。
 *
 * 规则：
 * - 原生的"编程错误"类型（TypeError / ReferenceError / RangeError /
 *   SyntaxError / EvalError / URIError）一律不展示原文，只展示调用方给的
 *   兜底文案；原文交给 `onInternalError` 回调（默认写 console.error），
 *   便于排查而不打扰用户。
 * - 其余情况（ApiError、以及我们自己 `new Error("中文文案")` 抛出的业务错误）
 *   保持原有行为，展示 `error.message`。
 * - 空白 message 视为无效，走兜底文案。
 *
 * 这样做不改变任何正常业务错误的展示文案，只堵住"内部错误外泄"这一条。
 */

const PROGRAMMING_ERROR_TYPES = [
  TypeError,
  ReferenceError,
  RangeError,
  SyntaxError,
  EvalError,
  URIError,
] as const;

function isProgrammingError(error: unknown): boolean {
  return PROGRAMMING_ERROR_TYPES.some((ErrorType) => error instanceof ErrorType);
}

export function userFacingErrorMessage(
  error: unknown,
  fallback: string,
  onInternalError: (error: unknown) => void = (raw) => {
    // 只进控制台/遥测，不进 UI。
    console.error("[fundpilot] 内部错误已对用户隐藏：", raw);
  },
): string {
  if (isProgrammingError(error)) {
    onInternalError(error);
    return fallback;
  }
  if (error instanceof Error) {
    const message = error.message.trim();
    if (message) {
      return message;
    }
  }
  return fallback;
}
