"use client";

import { FileImage, ScanLine, WandSparkles } from "lucide-react";

type UploadDropzoneProps = {
  rawText: string;
  isBusy: boolean;
  onRawTextChange: (value: string) => void;
  onFileChange: (file: File | null) => void;
  onParse: () => void;
  onLoadSample: () => void;
};

export function UploadDropzone({
  rawText,
  isBusy,
  onRawTextChange,
  onFileChange,
  onParse,
  onLoadSample,
}: UploadDropzoneProps) {
  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-blue-600 text-white">
              <ScanLine size={22} />
            </div>
            <h2 className="text-xl font-black text-slate-950">截图识别工作台</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">
              上传支付宝基金截图，或先粘贴 OCR 文本。识别结果会进入下方校对表，再由风控规则和 DeepSeek 生成日报。
            </p>
          </div>
          <button
            type="button"
            onClick={onLoadSample}
            data-testid="load-sample"
            className="inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700 shadow-sm transition hover:border-blue-200 hover:text-blue-700"
          >
            <WandSparkles size={16} />
            载入样例
          </button>
        </div>

        <label className="group flex min-h-40 flex-col items-center justify-center rounded-[24px] border border-dashed border-blue-300 bg-white/80 px-5 py-8 text-center transition hover:border-blue-500 hover:bg-blue-50/70">
          <FileImage className="mb-3 text-blue-600" size={34} />
          <span className="text-base font-black text-slate-950">选择支付宝基金截图</span>
          <span className="mt-1 text-sm text-slate-500">PNG / JPG，资产截图只保存在本地项目目录</span>
          <input
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
          />
        </label>

        <textarea
          value={rawText}
          onChange={(event) => onRawTextChange(event.target.value)}
          placeholder="也可以先把截图文字粘贴到这里：基金名称、代码、持有金额、收益率..."
          className="min-h-32 w-full resize-y rounded-3xl border border-slate-200 bg-white px-5 py-4 text-sm leading-6 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
        />

        <button
          type="button"
          onClick={onParse}
          disabled={isBusy}
          data-testid="parse-ocr"
          className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-blue-600 px-5 py-3 text-sm font-black text-white shadow-[0_16px_36px_rgba(23,119,255,0.28)] transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
        >
          <ScanLine size={18} />
          {isBusy ? "识别中..." : "开始识别并生成校对表"}
        </button>
      </div>
    </section>
  );
}
