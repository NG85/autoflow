from app.crm.save_engine import _normalize_risk_info


def test_normalize_online_empty_json_block():
    """线上卡片风险字段实测：空的 ```json 代码块。"""
    raw = """```json

```"""
    assert _normalize_risk_info(raw) == ""


def test_normalize_empty_evidences_and_risk():
    assert _normalize_risk_info('{"evidences": [], "risk": ""}') == ""
    assert _normalize_risk_info('{"evidences": [], "risk": "客户有顾虑"}') == ""
    assert _normalize_risk_info('{"risk": ""}') == ""
    assert _normalize_risk_info("{}") == ""
    assert _normalize_risk_info("[]") == ""
    assert _normalize_risk_info("```json\n{bad}\n```") == ""


def test_normalize_evidences_and_risk_with_content():
    raw = (
        '{"evidences": ["客户预算尚未批复", "签约节奏存在不确定性"], '
        '"risk": "客户预算尚未批复，签约节奏存在不确定性"}'
    )
    assert _normalize_risk_info(raw) == "客户预算尚未批复，签约节奏存在不确定性"


def test_normalize_evidences_fallback_when_risk_empty():
    raw = '{"evidences": ["客户对交付周期表示担忧", "希望延期评估"], "risk": ""}'
    assert _normalize_risk_info(raw) == "客户对交付周期表示担忧；希望延期评估"


def test_normalize_fenced_evidences_json():
    raw = """```json
{"evidences": ["客户对交付周期表示担忧"], "risk": "客户对交付周期表示担忧，希望延期评估。"}
```"""
    assert _normalize_risk_info(raw) == "客户对交付周期表示担忧，希望延期评估。"


def test_normalize_json_wrapped_in_prose():
    raw = (
        '分析结果如下：\n'
        '{"evidences": ["客户内部决策人尚未对齐"], '
        '"risk": "客户内部决策人尚未对齐，推进节奏存在不确定性"}\n以上。'
    )
    assert _normalize_risk_info(raw) == "客户内部决策人尚未对齐，推进节奏存在不确定性"


def test_normalize_legacy_risk_only_json():
    """兼容仅有 risk 字段的旧形态。"""
    raw = '{"risk": "客户预算尚未批复，签约节奏存在不确定性"}'
    assert _normalize_risk_info(raw) == "客户预算尚未批复，签约节奏存在不确定性"


def test_normalize_non_json_returns_empty():
    """非 JSON 不再兜底进卡片，与质量评估解析失败策略一致。"""
    assert _normalize_risk_info("客户担心历史数据迁移失败，要求先给回滚预案。") == ""
    assert _normalize_risk_info("NONE") == ""
