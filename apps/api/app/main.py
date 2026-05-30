from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import get_report, list_reports, save_report
from app.models import AnalysisRequest
from app.services.deepseek_client import DeepSeekClient
from app.services.fund_data import FundDataService
from app.services.market_context import MarketContextService
from app.services.ocr_engine import OcrEngine
from app.services.ocr_parser import parse_holdings_from_text
from app.services.risk import evaluate_portfolio_risk


settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/ocr")
async def parse_ocr(
    raw_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    text = raw_text or ""
    upload_path: Path | None = None

    if file is not None and file.filename:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = settings.upload_dir / Path(file.filename).name
        upload_path.write_bytes(await file.read())
        if not text:
            try:
                text = OcrEngine().extract_text(upload_path)
            except Exception as exc:
                return {
                    "raw_text": "",
                    "upload_path": str(upload_path),
                    "holdings": [],
                    "error": f"OCR 识别失败：{exc}",
                }

    holdings = parse_holdings_from_text(text)
    return {
        "raw_text": text,
        "upload_path": str(upload_path) if upload_path else None,
        "holdings": [holding.model_dump() for holding in holdings],
    }


@app.post("/api/analyze")
def analyze(request: AnalysisRequest) -> dict:
    if not request.holdings:
        raise HTTPException(status_code=400, detail="至少需要一条基金持仓")

    risk = evaluate_portfolio_risk(request.holdings, request.profile)
    snapshots = FundDataService().get_snapshots(request.holdings)
    market_context = MarketContextService().collect(request.holdings)
    report = DeepSeekClient().generate_report(request, risk, snapshots, market_context)
    save_report(report)
    return report.model_dump(mode="json")


@app.get("/api/reports")
def reports() -> list[dict]:
    return list_reports()


@app.get("/api/reports/{report_id}")
def report_detail(report_id: str) -> dict:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report
