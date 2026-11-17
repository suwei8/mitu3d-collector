import json

from scripts.notify_telegram import format_message, format_xinfeng_message, latest_record

RECORD = {
    "issue": "2026264",
    "status": "candidate",
    "draw_result": "119",
    "fields": {
        "beijing": "渡浅滩",
        "beijing_alt": "鸡大腿",
        "taihu": "官封白吃侯",
        "trial_number": "741",
        "focus": "169",
        "gold": "1",
        "corresponding": "236",
        "bottom_focus": ["2", "4"],
        "bottom_gold": "7",
    },
    "collected_at": "2026-10-05T12:17:55.958519+00:00",
}


def test_format_message_full_record():
    msg = format_message(RECORD, "https://example.com/x.md")
    assert "<b>福彩3D 2026264期</b>（candidate）" in msg
    assert "开奖号：<b>119</b>" in msg
    assert "试机号 741 · 关注码 169 · 金码 1 · 对应码 236" in msg
    assert "太湖 官封白吃侯" in msg
    assert "北京 渡浅滩 · 另版 鸡大腿" in msg
    assert "底部关注码 2、4 · 底部金码 7" in msg
    assert "采集于 10-05 20:17（北京）" in msg
    assert 'href="https://example.com/x.md"' in msg


def test_format_message_partial_record():
    rec = {
        "issue": "2026265",
        "status": "candidate",
        "fields": {"taihu": "某某一语"},
        "collected_at": "2026-10-06T10:00:00+00:00",
    }
    msg = format_message(rec)
    assert "2026265期" in msg
    assert "太湖 某某一语" in msg
    assert "开奖号" not in msg
    assert "试机号" not in msg
    assert "采集于 10-06 18:00（北京）" in msg


XF_RECORD = {
    "issue": "2026305",
    "status": "candidate",
    "fields": {"trial_number": "741"},
    "xinfeng": {
        "cz89": {
            "fields": {"seven": "1245678", "six": "124567", "five": "12457", "dan": "217"},
            "ocr_ok": True,
        },
        "e46": {
            "fields": {"seven": "1275460", "six": "127546", "five": "12754", "dan": "251"},
            "ocr_ok": False,
        },
    },
    "collected_at": "2026-11-16T13:30:00+00:00",
}


def test_xinfeng_split_out_of_main_message():
    """信封快报不进主消息，主消息保持原格式。"""
    msg = format_message(XF_RECORD)
    assert "信封快报" not in msg
    assert "1245678" not in msg


def test_format_xinfeng_message_both_versions():
    msg = format_xinfeng_message(XF_RECORD, "https://example.com/x.md")
    assert "<b>福彩3D 2026305期 · 信封快报</b>" in msg
    assert "信封快报·牛彩版（升序）\n七码 1245678\n六码 124567\n五码 12457\n胆码 217" in msg
    assert "信封快报·打黑版（乱序）（部分待复核）\n七码 1275460\n六码 127546\n五码 12754\n胆码 251" in msg
    assert 'href="https://example.com/x.md"' in msg


def test_format_xinfeng_message_partial_ocr_flag():
    rec = {"issue": "2026306", "xinfeng": {"e46": {"fields": {"seven": "1?34?62"}, "ocr_ok": False}}}
    msg = format_xinfeng_message(rec)
    assert "信封快报·打黑版（乱序）（部分待复核）\n七码 1?34?62" in msg
    assert "牛彩版" not in msg


def test_format_xinfeng_message_none_without_xinfeng():
    rec = {"issue": "2026265", "status": "candidate", "fields": {"taihu": "某某一语"}}
    assert format_xinfeng_message(rec) is None
    assert "信封快报" not in format_message(rec)


def test_merged_message_inline_xinfeng():
    """合并版：主格式 + 信封快报块（逐字段换行）。"""
    msg = format_message(XF_RECORD, include_xinfeng=True)
    assert "试机号 741" in msg
    assert "信封快报·牛彩版（升序）\n七码 1245678\n六码 124567\n五码 12457\n胆码 217" in msg
    assert "信封快报·打黑版（乱序）（部分待复核）\n七码 1275460\n六码 127546\n五码 12754\n胆码 251" in msg


def test_latest_record_picks_max_issue(tmp_path):
    raw = tmp_path / "raw" / "2026"
    raw.mkdir(parents=True)
    for issue in ("2026263", "2026264", "2026262"):
        (raw / f"{issue}.json").write_text(json.dumps({"issue": issue}))
    assert latest_record(tmp_path)["issue"] == "2026264"


def test_latest_record_empty(tmp_path):
    assert latest_record(tmp_path) is None
