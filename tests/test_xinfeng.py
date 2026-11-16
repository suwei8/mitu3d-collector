import json
from pathlib import Path

import pytest

from scripts import xinfeng
from scripts.collect import build_record, content_unchanged, render_md

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_fields_cz89_fixture():
    r = xinfeng.extract_fields(
        (FIXTURES / "xinfeng_cz89_2026304.jpg").read_bytes(), "cz89"
    )
    assert r["fields"] == {
        "seven": "0234678",
        "six": "013678",
        "five": "01678",
        "dan": "670",
    }
    assert r["ocr_ok"] is True


def test_extract_fields_e46_fixture():
    r = xinfeng.extract_fields(
        (FIXTURES / "xinfeng_e46_2026305.jpg").read_bytes(), "e46"
    )
    assert r["fields"] == {
        "seven": "1275460",
        "six": "127546",
        "five": "12754",
        "dan": "251",
    }
    assert r["ocr_ok"] is True


def test_extract_fields_rejects_blank_image():
    """全空图应拒识为 '?' 而非误识出数字。"""
    import io

    import numpy as np
    from PIL import Image

    black = Image.fromarray(np.full((433, 450), 230, dtype="uint8"))
    buf = io.BytesIO()
    black.save(buf, format="JPEG")
    r = xinfeng.extract_fields(buf.getvalue(), "cz89")
    assert r["fields"]["seven"] == "???????"
    assert r["fields"]["dan"] is None
    assert r["ocr_ok"] is False


def test_discover_cz89_xinfeng_parses_listing(monkeypatch):
    listing = """
    <a href="/read_1.htm">钱庄快报2026306期3d图谜</a>
    <a href="/read_2.htm">信封快报2026306期3d图谜</a>
    <a href="/read_3.htm">信封快报2026305期3d图谜</a>
    """
    monkeypatch.setattr(xinfeng, "_get", lambda url, **kw: listing)
    assert (
        xinfeng.discover_cz89_xinfeng("2026305")
        == "https://m.cz89.com/read_3.htm"
    )
    assert (
        xinfeng.discover_cz89_xinfeng("2026306")
        == "https://m.cz89.com/read_2.htm"
    )
    assert xinfeng.discover_cz89_xinfeng("2026307") is None


def test_fetch_cz89_reads_lazyload_img(monkeypatch):
    article = (
        '<img class="min-height lazy" '
        'data-original="https://pic.cz89.com/upload/20261113/abc.png" />'
    )
    monkeypatch.setattr(xinfeng, "_get", lambda url, **kw: article)
    monkeypatch.setattr(xinfeng, "fetch_image_bytes", lambda url, *a: b"JPG")
    got = xinfeng.fetch_cz89("2026304", "https://m.cz89.com/read_10992928.htm")
    assert got["image_url"] == "https://pic.cz89.com/upload/20261113/abc.png"
    assert got["layout"] == "cz89"


def test_fetch_e46_reads_img_and_resolves(monkeypatch):
    page = '<img src="http://tuku.55123.cn/tu/2026305/E46.jpg" onerror="chgImg(this)" />'
    monkeypatch.setattr(xinfeng, "_get", lambda url, **kw: page)
    calls = []

    def fake_fetch(url, fallback=None):
        calls.append(fallback)
        return b"JPG"

    monkeypatch.setattr(xinfeng, "fetch_image_bytes", fake_fetch)
    got = xinfeng.fetch_e46("2026305")
    assert got["image_url"] == "http://tuku.55123.cn/tu/2026305/E46.jpg"
    assert calls == [("tuku.55123.cn", xinfeng.E46_RESOLVE_IP)]


def test_collect_xinfeng_source_failure_isolated(tmp_path, monkeypatch):
    def boom(issue, url=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(xinfeng, "fetch_cz89", boom)
    monkeypatch.setattr(xinfeng, "fetch_e46", lambda issue: {
        "page_url": "https://m.55125.cn/tuku/2026305/E46.htm",
        "image_url": "http://tuku.55123.cn/tu/2026305/E46.jpg",
        "image": (FIXTURES / "xinfeng_e46_2026305.jpg").read_bytes(),
        "layout": "e46",
    })
    got = xinfeng.collect_xinfeng("2026305", tmp_path)
    assert "cz89" not in got
    assert got["e46"]["fields"]["dan"] == "251"
    assert (tmp_path / "images/2026/2026305/xinfeng-e46.jpg").exists()


def test_build_record_merges_xinfeng(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.collect.ROOT", tmp_path)
    xf = {
        "e46": {
            "page_url": "https://m.55125.cn/tuku/2026305/E46.htm",
            "image_url": "http://tuku.55123.cn/tu/2026305/E46.jpg",
            "image": "images/2026/2026305/xinfeng-e46.jpg",
            "fields": {"seven": "1275460", "six": "127546", "five": "12754", "dan": "251"},
            "ocr_ok": True,
        }
    }
    rec = build_record("2026305", {}, {}, xinfeng=xf)
    assert rec["xinfeng"]["e46"]["fields"]["seven"] == "1275460"
    assert rec["sources"]["caiba-e46"]["image"] == "images/2026/2026305/xinfeng-e46.jpg"
    md = render_md(rec)
    assert "信封快报" in md
    assert "1275460" in md
    assert "xinfeng-e46.jpg" in md


def test_content_unchanged_ignores_xinfeng_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.collect.ROOT", tmp_path)
    xf = {"e46": {"page_url": "u", "fields": {"seven": "1"}, "ocr_ok": True}}
    r1 = build_record("2026305", {}, {}, xinfeng=xf)
    (tmp_path / "raw" / "2026").mkdir(parents=True)
    (tmp_path / "raw" / "2026" / "2026305.json").write_text(json.dumps(r1), encoding="utf-8")
    r2 = build_record("2026305", {}, {}, xinfeng=xf)
    assert content_unchanged(r1, r2)
