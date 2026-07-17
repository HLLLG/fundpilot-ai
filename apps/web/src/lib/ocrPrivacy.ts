export const OCR_PRIVACY_COPY = {
  shortLabel: "服务端识别方式可配置",
  trustTitle: "截图传输与识别路径透明可控",
  trustDescription:
    "截图会发送到本应用服务端：local 模式由应用服务端本地 OCR 处理，不转发给第三方 OCR；auto 模式配置云端 OCR 后，服务端还会将截图发送至阿里云百炼完成文字识别。",
  uploadNotice:
    "截图由本应用服务端处理；如启用云端识别，会发送至阿里云百炼。投研模型仅接收结构化持仓。",
} as const;
