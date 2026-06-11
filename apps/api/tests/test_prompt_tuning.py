from app.services.prompt_tuning import resolve_prompt_tuning_hints


def test_prompt_tuning_tightens_when_aggressive_miss_high(monkeypatch):
    monkeypatch.setattr(
        "app.services.prompt_tuning.build_recommendation_accuracy",
        lambda **kwargs: {
            "paired_days": 5,
            "by_style": {
                "tactical": {
                    "reversal": {
                        "up_then_down_count": 4,
                        "up_then_down_aggressive_miss": 3,
                    }
                }
            },
        },
    )
    result = resolve_prompt_tuning_hints()
    assert result["tighten_tactical"] is True
    assert result["hints"]
