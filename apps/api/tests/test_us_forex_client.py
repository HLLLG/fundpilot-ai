from __future__ import annotations

from types import SimpleNamespace

from app.services import us_forex_client


def test_parse_json_stdout_uses_structured_tail_after_provider_diagnostic():
    stdout = '[pn=0] 接口返回异常: {"ResultCode": "403"}\n{"error": "empty"}\n'

    assert us_forex_client._parse_json_stdout(stdout) == {"error": "empty"}


def test_boc_sina_script_uses_rolling_dates_instead_of_akshare_2023_defaults():
    assert "start_date=start.strftime" in us_forex_client._BOC_SINA_SCRIPT
    assert "end_date=end.strftime" in us_forex_client._BOC_SINA_SCRIPT


def test_run_akshare_treats_noisy_source_error_as_expected_fallback(monkeypatch, caplog):
    def fake_run(*_args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return SimpleNamespace(
            returncode=0,
            stdout='[pn=0] 接口返回异常: 403\n{"error": "empty"}\n',
            stderr="",
        )

    monkeypatch.setattr(us_forex_client.subprocess, "run", fake_run)

    assert us_forex_client._run_akshare("ignored", label="fx_quote_baidu") is None
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


def test_run_akshare_returns_payload_after_provider_diagnostic(monkeypatch):
    monkeypatch.setattr(
        us_forex_client.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='provider note\n{"columns": ["美元"], "records": [{"美元": 689.51}]}\n',
            stderr="",
        ),
    )

    assert us_forex_client._run_akshare("ignored", label="currency_boc_safe") == {
        "columns": ["美元"],
        "records": [{"美元": 689.51}],
    }
