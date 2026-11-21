#!/usr/bin/env python3
"""「虎头信封图」主线：信封快报产品族图像采集 + 字段抽取。

溯源结论（见归档仓 analysis/provenance-hutou-xinfeng.md）：群图主体来自
「中国福彩3D信封快报」同模板产品族，至少两个数值独立的版本：

- cz89 版（牛彩网图谜频道，数字按升序展示）：每晚一篇「信封快报YYYYXXX期
  3d图谜」文章，列表在 m.cz89.com/3d/item_3.htm（m.01p.net 同构镜像）。
- e46 版（彩吧图库「北京3D信封快报·打黑版」，保留发行方乱序）：页面
  m.55125.cn/tuku/{issue}/E46.htm，图床 tuku.55123.cn 存在死 DNS IP，
  直连失败时回退 curl --resolve 到其前端 IP 43.240.60.114。

字段（七码/六码/精选五码/胆码）只渲染在 JPG 像素里，网页不含文本，
因此用「固定版式坐标切格 → 字形二值化 → 模板库最近邻比对」抽取。
模板库 xinfeng_templates.json 由已人工核验的历史期样本构建；识别失败
的字段记 None/'?' + ocr_ok=false，原图始终入档（images/YYYY/ISSUE/）可复核。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "fc3d-collector/0.1 (+https://github.com/suwei8/mitu3d-collector)"
TEMPLATE_PATH = Path(__file__).resolve().parent / "xinfeng_templates.json"

CZ89_LIST_URL = "https://m.cz89.com/3d/item_3.htm"
CZ89_MIRROR_HOST = "https://m.01p.net"
E46_PAGE_URL = "https://m.55125.cn/tuku/{issue}/E46.htm"
E46_RESOLVE_IP = "43.240.60.114"  # m.55125.cn 前端；tuku.55123.cn 存在死 DNS IP

# 版式坐标（宽高的比例值），来自同一模板实测。
# cell_x0/cell_pitch/cell_w 是格心列：第 k 格内区 [x0+k*pitch, x0+k*pitch+w]。
LAYOUTS = {
    "cz89": {  # 450x433 基准
        "cell_x0": 94 / 450, "cell_w": 36 / 450, "cell_pitch": 40 / 450,
        "rows": {"seven": (0.189, 0.254, 7), "six": (0.282, 0.351, 6), "five": (0.378, 0.443, 5)},
        "circles": (0.40, 0.47, 0.94, 0.78),  # (x0, y0, x1, y1)
    },
    "e46": {  # 1000x1203 基准
        "cell_x0": 243 / 1000, "cell_w": 57 / 1000, "cell_pitch": 78 / 1000,
        "rows": {"seven": (0.123, 0.168, 7), "six": (0.185, 0.231, 6), "five": (0.249, 0.289, 5)},
        "circles": (0.47, 0.27, 0.88, 0.48),
    },
}

DARK = 140
CANVAS_H, CANVAS_W = 44, 32
_ACCEPT_SCORE = 0.22


# ---------------------------------------------------------------- templates

def _unpack(hexstr: str, ch: int, cw: int):
    import numpy as np

    bits = bin(int(hexstr, 16))[2:].zfill(ch * cw)
    return np.array([int(c) for c in bits], dtype=np.uint8).reshape(ch, cw)


_TEMPLATES: dict[str, list] | None = None


def templates() -> dict[str, list]:
    global _TEMPLATES
    if _TEMPLATES is None:
        data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        ch, cw = data["canvas"]
        _TEMPLATES = {
            d: [_unpack(h, ch, cw) for h in hexes]
            for d, hexes in data["templates"].items()
        }
    return _TEMPLATES


# ------------------------------------------------------------- glyph extract

def _detect_grid(gray, layout: dict, row: str) -> list[tuple[int, int]] | None:
    """在行带内拟合竖边框网格，返回 n 个格子的 (x0,x1) 内区；失败返回 None。

    同源图存在不同导出尺寸（450x433 / 494x479 等），比例坐标会偏移。
    先取「列暗度>65%」的候选边框线，再穷举 (起点, 间距) 找一条覆盖
    n+1 个边框位的等距网格——允许至多一条边框由外推补齐（外侧框
    常偏淡），要求至少 n 个边框位有实测线佐证。
    """
    import numpy as np

    h, w = gray.shape
    fy0, fy1, n = layout["rows"][row]
    band = gray[int(h * fy0):int(h * fy1), :]
    bh = band.shape[0]
    colsum = (band < 210).sum(0)
    cand = np.where(colsum > bh * 0.65)[0]
    lines: list[int] = []
    for x in cand:
        if lines and x - lines[-1] <= 3:
            continue
        lines.append(int(x))
    if len(lines) < n:
        return None
    exp_x0 = layout["cell_x0"] * w
    pitch0 = layout["cell_pitch"] * w
    best = None
    for p0 in range(int(exp_x0) - 25, int(exp_x0) + 25):
        for pi in np.arange(pitch0 * 0.85, pitch0 * 1.15, 0.5):
            borders = [p0 + k * pi for k in range(n + 1)]
            if borders[-1] > w * 0.93:
                continue
            matched, dark = 0, 0.0
            for b in borders:
                near = [l for l in lines if abs(l - b) <= 4]
                if near:
                    matched += 1
                    dark += float(colsum[min(near, key=lambda l: abs(l - b))])
            if matched < n:
                continue
            score = (matched, dark, -abs(p0 - exp_x0))
            if best is None or score > best[0]:
                best = (score, borders)
    if best is None:
        return None
    seg = [int(round(b)) for b in best[1]]
    return [(seg[k] + 2, seg[k + 1] - 2) for k in range(n)]


def cell_glyphs(gray, layout: dict, row: str) -> list:
    """按格位裁出该行 n 个数字字形（缺格给 None）。

    先用行内竖边框自校准格网（容忍不同导出尺寸），失败回退比例坐标；
    格子边框排除在格内区之外；孤立小点由 min 面积过滤。
    """
    import numpy as np

    h, w = gray.shape
    fy0, fy1, n = layout["rows"][row]
    cells = _detect_grid(gray, layout, row)
    if cells is None:
        cells = [
            (
                int(w * (layout["cell_x0"] + k * layout["cell_pitch"])),
                int(w * (layout["cell_x0"] + k * layout["cell_pitch"] + layout["cell_w"])),
            )
            for k in range(n)
        ]
    out = []
    for x0, x1 in cells:
        cell = gray[int(h * fy0):int(h * fy1), x0:x1]
        b = cell < DARK
        if b.sum() < 20:
            out.append(None)
            continue
        lab, sizes = _components(b)
        keep = set()
        for i, sz in enumerate(sizes):
            if sz < 6:
                continue  # JPEG 噪点
            ys2, xs2 = np.where(lab == i + 1)
            gh, gw = int(ys2.max() - ys2.min()) + 1, int(xs2.max() - xs2.min()) + 1
            touches_edge = (ys2[0] == 0 or xs2[0] == 0
                            or ys2[-1] == b.shape[0] - 1 or xs2[-1] == b.shape[1] - 1)
            if touches_edge and (gh >= b.shape[0] * 0.85 or gw >= b.shape[1] * 0.85):
                continue  # 贴边的长条边框残线
            keep.add(i + 1)
        b = np.isin(lab, list(keep))
        if b.sum() < 20:
            out.append(None)
            continue
        ys = np.where(b.any(1))[0]
        xs = np.where(b.any(0))[0]
        out.append(b[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1].astype("uint8"))
    return out


def _components(mask):
    """8-连通域标记，返回 (label 图, 各域面积)。"""
    import numpy as np

    h, w = mask.shape
    lab = np.zeros(mask.shape, dtype="int64")
    sizes: list[int] = []
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not lab[y, x]:
                n = len(sizes) + 1
                q = deque([(y, x)])
                lab[y, x] = n
                c = 0
                while q:
                    yy, xx = q.popleft()
                    c += 1
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = yy + dy, xx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                                lab[ny, nx] = n
                                q.append((ny, nx))
                sizes.append(c)
    return lab, sizes


def circle_glyphs(rgb, x0: int, y0: int, x1: int, y1: int, n: int = 3) -> list:
    """在区域里找 n 个蓝色圆胆，返回圆内白色数字的二值块。

    圆心取蓝色行密度最大处，方窗按直径比例裁中心，再按连通域剔除
    圆环高光/装饰碎片，只保留构成数字的大连通块。
    """
    import numpy as np

    reg = rgb[y0:y1, x0:x1].astype("int16")
    blue = (reg[:, :, 2] > reg[:, :, 0] + 40) & (reg[:, :, 2] > reg[:, :, 1] + 30) & (reg[:, :, 2] > 100)
    prof = blue.sum(0)
    segs = []
    x = 0
    w = reg.shape[1]
    while x < w:
        if prof[x] > 8:
            s = x
            while x < w and prof[x] > 8:
                x += 1
            if x - s > 12:
                segs.append((s, x - 1))
        x += 1
    out = []
    for s, e in segs[-n:]:
        sub = blue[:, s:e + 1]
        cw = e - s + 1
        cy = int(np.argmax(sub.sum(1)))
        half = int(cw * 0.34)
        cx = s + cw // 2
        sq = reg[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
        lum = (sq[:, :, 0] + sq[:, :, 1] + sq[:, :, 2]) / 3
        white = ((lum > 180) & (sq[:, :, 0] > 160) & (sq[:, :, 2] > 150)).astype("uint8")
        lab, sizes = _components(white)
        if not sizes or max(sizes) < 15:
            continue
        keep = {i + 1 for i, v in enumerate(sizes) if v >= max(sizes) * 0.15}
        white = np.array([[1 if lab[y, x] in keep else 0 for x in range(white.shape[1])]
                          for y in range(white.shape[0])], dtype="uint8")
        ys2 = white.any(1).nonzero()[0]
        xs2 = white.any(0).nonzero()[0]
        if len(ys2) < 10:
            continue
        out.append(white[ys2[0]:ys2[-1] + 1, xs2[0]:xs2[-1] + 1])
    return out


# -------------------------------------------------------------- classify

def _normalize(glyph):
    """字形等比缩放到 44x32 画布内（保宽高比、水平居中、垂直铺满）。"""
    from PIL import Image
    import numpy as np

    h, w = glyph.shape
    scale = min(CANVAS_H / h, CANVAS_W / w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    im = Image.fromarray((255 - glyph * 255).astype("uint8")).resize((nw, nh), Image.LANCZOS)
    arr = (np.array(im) < 128).astype("uint8")
    canvas = np.zeros((CANVAS_H, CANVAS_W), "uint8")
    oy = (CANVAS_H - nh) // 2
    ox = (CANVAS_W - nw) // 2
    canvas[oy:oy + nh, ox:ox + nw] = arr
    return canvas


def _diff(a, b) -> float:
    """±3px 平移对齐取最小异或比（吸收格位/圆心定位的抖动）。"""
    import numpy as np

    best = 1.0
    for dy in (-3, -2, -1, 0, 1, 2, 3):
        for dx in (-3, -2, -1, 0, 1, 2, 3):
            score = float((np.roll(np.roll(a, dy, 0), dx, 1) != b).sum()) / a.size
            if score < best:
                best = score
    return best


def classify(glyph, lib: dict[str, list]) -> tuple[str | None, float]:
    norm = _normalize(glyph)
    if norm.sum() < 30:
        return None, 1.0
    best_d, best = None, 1.0
    for d, tpls in lib.items():
        for t in tpls:
            score = _diff(norm, t)
            if score < best:
                best, best_d = score, d
    return (best_d if best < _ACCEPT_SCORE else None), best


def extract_fields(image_bytes: bytes, layout_key: str) -> dict[str, Any]:
    """从一张信封快报图中抽出四组字段；无法识别的位置记 '?'/None。"""
    import io

    import numpy as np
    from PIL import Image

    layout = LAYOUTS[layout_key]
    im = Image.open(io.BytesIO(image_bytes))
    gray = np.array(im.convert("L"))
    rgb = np.array(im.convert("RGB"))
    h, w = gray.shape
    lib = templates()

    fields: dict[str, Any] = {}
    scores: dict[str, float] = {}
    for row in ("seven", "six", "five"):
        digs, worst = [], 0.0
        for g in cell_glyphs(gray, layout, row):
            if g is None:
                digs.append("?")
                continue
            d, sc = classify(g, lib)
            digs.append(d if d is not None else "?")
            worst = max(worst, sc)
        fields[row] = "".join(digs)
        scores[row] = round(worst, 4)

    digs, worst = [], 0.0
    cx0, cy0, cx1, cy1 = layout["circles"]
    for g in circle_glyphs(rgb, int(w * cx0), int(h * cy0), int(w * cx1), int(h * cy1)):
        d, sc = classify(g, lib)
        digs.append(d if d is not None else "?")
        worst = max(worst, sc)
    fields["dan"] = "".join(digs) if digs else None
    scores["dan"] = round(worst, 4)

    # 复试行内数字必然不重复（各行是独立预测，无逐级子集关系）；
    # 出现重复即误识，降级待复核而非自信推错值。
    rows = [fields[r] for r in ("seven", "six", "five")]
    consistent = all(v is None or len(set(v)) == len(v) for v in rows)
    ok = all(v is not None and "?" not in v for v in fields.values()) and consistent
    return {"fields": fields, "ocr_ok": ok, "worst_score": scores}


# ---------------------------------------------------------------- fetching

def _get(url: str, timeout: int = 25, binary: bool = False):
    r = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout,
    )
    r.raise_for_status()
    if binary:
        return r.content
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def _curl_resolve_bytes(url: str, host: str, ip: str) -> bytes:
    scheme = urlparse(url).scheme or "https"
    port = "443" if scheme == "https" else "80"
    out = subprocess.run(
        [
            "curl", "-fsSL", "--max-time", "30", "-A", UA,
            "--resolve", f"{host}:{port}:{ip}",
            url, "-o", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    if not out:
        raise requests.RequestException("curl --resolve returned empty body")
    return out


def fetch_image_bytes(url: str, resolve_fallback: tuple[str, str] | None = None) -> bytes:
    """requests 直连；失败且给了 (host, ip) 兜底时走 curl --resolve。

    http:// 会额外生成一个 https:// 候选（该图床 443 通而 80/DNS 有死 IP）。
    """
    urls = [url] + (["https://" + url[len("http://"):]] if url.startswith("http://") else [])
    last_exc: Exception | None = None
    for u in urls:
        try:
            return _get(u, binary=True)
        except requests.RequestException as exc:
            last_exc = exc
    if not resolve_fallback:
        raise last_exc  # type: ignore[misc]
    for u in urls:
        try:
            return _curl_resolve_bytes(u, *resolve_fallback)
        except subprocess.SubprocessError as exc:
            last_exc = exc
    raise requests.RequestException(f"image fetch failed: {last_exc}")


def fetch_e46(issue: str) -> dict[str, Any] | None:
    """彩吧图库 E46「北京3D信封快报·打黑版」。返回 None=未发布/不可达。"""
    page_url = E46_PAGE_URL.format(issue=issue)
    try:
        html = _get(page_url)
    except requests.RequestException as exc:
        print(f"[warn] caiba-e46 page unavailable: {exc}")
        return None
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img", src=re.compile(r"tuku\.55123\.cn/"))
    if not img:
        print(f"[warn] caiba-e46 {issue}: page has no image tag")
        return None
    img_url = urljoin(page_url, img["src"])
    host = urlparse(img_url).hostname or ""
    try:
        blob = fetch_image_bytes(img_url, (host, E46_RESOLVE_IP))
    except (requests.RequestException, subprocess.SubprocessError) as exc:
        print(f"[warn] caiba-e46 image failed: {exc}")
        return None
    return {"page_url": page_url, "image_url": img_url, "image": blob, "layout": "e46"}


_XINFENG_TITLE_RE = re.compile(r"信封快报0*(20\d{5})期3d图谜")


def discover_cz89_xinfeng(issue: str, max_pages: int = 200,
                          base_urls: tuple[str, ...] = (CZ89_LIST_URL, CZ89_MIRROR_HOST + "/3d/item_3.htm")) -> str | None:
    """在 3D图谜列表里按期号找「信封快报」文章；主源 m.cz89.com，镜像 m.01p.net。

    列表按时间倒序：当期文章 1-2 页内命中即返回；历史期继续翻页直到
    连续 2 页最大期号 < 目标（与 index 同一停止条件）。
    另按「首页最新期号−目标期号≈距今天数」估深度上限，未发布的新期
    不会无限翻页（期号未发→深度估算很小→几页即停）。
    """
    for base in base_urls:
        old_streak = 0
        depth_cap: int | None = None
        for page in range(1, max_pages + 1):
            url = base if page == 1 else f"{base}?p={page}"
            try:
                html = _get(url)
            except requests.RequestException as exc:
                print(f"[warn] cz89-xinfeng list {url}: {exc}")
                break
            soup = BeautifulSoup(html, "html.parser")
            page_max = None
            for a in soup.find_all("a", href=True):
                title = re.sub(r"\s+", "", a.get_text())
                im = _ANY_ISSUE_RE.search(title)
                if im:
                    iss = im.group(1)
                    page_max = iss if page_max is None or iss > page_max else page_max
                m = _XINFENG_TITLE_RE.search(title)
                if m and m.group(1) == issue:
                    return urljoin(url, a["href"])
            if page == 1 and page_max:
                age = max(0, int(page_max) - int(issue))
                depth_cap = min(max_pages, age * 30 + 40)
            if page_max is None or page_max < issue:
                old_streak += 1
                if old_streak >= 2:
                    break
            else:
                old_streak = 0
            if depth_cap is not None and page >= depth_cap:
                break
            time.sleep(0.3)
    print(f"[warn] cz89-xinfeng issue {issue} not found in listings")
    return None


_ANY_ISSUE_RE = re.compile(r"(20\d{5})期")


def index_cz89_xinfeng(lo: str, hi: str, max_pages: int = 200,
                       base_urls: tuple[str, ...] = (CZ89_LIST_URL, CZ89_MIRROR_HOST + "/3d/item_3.htm")) -> dict[str, str]:
    """翻 3D图谜列表建 {期号: 信封快报文章URL} 索引（供回填批量用）。

    列表按时间倒序且混排多系列文章；用每页出现的最大期号判定是否已越过
    区间（连续 2 页最大期号 < lo 即停），不必翻完整个列表。主源挂了换镜像整轮重扫。
    """
    for base in base_urls:
        idx, reached = _index_from_base(lo, hi, base, max_pages)
        if idx or reached:
            return idx
    return {}


def _index_from_base(lo: str, hi: str, base: str, max_pages: int) -> tuple[dict[str, str], bool]:
    """返回 (索引, 是否成功取到过页面)。reached=True 时即使索引为空也不再换镜像。"""
    idx: dict[str, str] = {}
    reached = False
    old_streak = 0
    for page in range(1, max_pages + 1):
        url = base if page == 1 else f"{base}?p={page}"
        try:
            html = _get(url)
            reached = True
        except requests.RequestException as exc:
            print(f"[warn] cz89-xinfeng index p{page}: {exc}")
            break
        soup = BeautifulSoup(html, "html.parser")
        page_max = None
        for a in soup.find_all("a", href=True):
            title = re.sub(r"\s+", "", a.get_text())
            m = _ANY_ISSUE_RE.search(title)
            if not m:
                continue
            iss = m.group(1)
            page_max = iss if page_max is None or iss > page_max else page_max
            xm = _XINFENG_TITLE_RE.search(title)
            if xm and lo <= iss <= hi and iss not in idx:
                idx[iss] = urljoin(url, a["href"])
        if page_max is None or page_max < lo:
            old_streak += 1
            if old_streak >= 2:
                break
        else:
            old_streak = 0
        if page % 20 == 0:
            print(f"[info] cz89-xinfeng index: page {page}, {len(idx)} issues")
        time.sleep(0.3)
    print(f"[info] cz89-xinfeng index ({base}): {len(idx)} issues in [{lo},{hi}]")
    return idx, reached


def fetch_cz89(issue: str, article_url: str | None = None) -> dict[str, Any] | None:
    """牛彩网图谜版「中国福彩3D信封快报」（升序版）。"""
    article_url = article_url or discover_cz89_xinfeng(issue)
    if not article_url:
        return None
    candidates = [article_url]
    m = re.search(r"(read_\d+\.htm)$", article_url)
    if m:
        candidates.append(f"{CZ89_MIRROR_HOST}/{m.group(1)}")
    img_url = None
    for url in candidates:
        try:
            html = _get(url)
        except requests.RequestException as exc:
            print(f"[warn] cz89-xinfeng article {url}: {exc}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img", attrs={"data-original": re.compile(r"pic\.cz89\.com/")})
        if not img:
            img = soup.find("img", src=re.compile(r"pic\.cz89\.com/"))
        if img:
            img_url = img.get("data-original") or img["src"]
            article_url = url
            break
    if not img_url:
        print(f"[warn] cz89-xinfeng {issue}: no image in article")
        return None
    # pic.cz89.com 本机间歇超时，重试两轮再放弃
    blob = None
    for attempt in range(3):
        try:
            blob = fetch_image_bytes(img_url)
            break
        except requests.RequestException as exc:
            print(f"[warn] cz89-xinfeng image attempt {attempt + 1}: {exc}")
            time.sleep(2)
    if blob is None:
        return None
    return {"page_url": article_url, "image_url": img_url, "image": blob, "layout": "cz89"}


# ---------------------------------------------------------------- collect

def _save_image(root: Path, issue: str, src: str, blob: bytes) -> str:
    d = root / "images" / issue[:4] / issue
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"xinfeng-{src}.jpg"
    path.write_bytes(blob)
    return str(path.relative_to(root))


def collect_xinfeng(issue: str, root: Path, cz89_url: str | None = None) -> dict[str, Any]:
    """采集两个版本的信封快报；只返回本轮成功抓到的版本。

    失败的版本不在返回里——由调用方与既有记录合并决定保留旧值与否。
    """
    out: dict[str, Any] = {}
    for src, fetcher in (("cz89", fetch_cz89), ("e46", fetch_e46)):
        try:
            got = fetcher(issue) if src == "e46" else fetcher(issue, cz89_url)
        except Exception as exc:  # 单源失败不拖垮整条主线
            print(f"[warn] xinfeng {src} collect failed: {exc}")
            continue
        if not got:
            continue
        ocr = extract_fields(got["image"], got["layout"])
        out[src] = {
            "page_url": got["page_url"],
            "image_url": got["image_url"],
            "image": _save_image(root, issue, src, got["image"]),
            **ocr,
        }
        print(f"[ok] xinfeng {src}: {ocr['fields']} ocr_ok={ocr['ocr_ok']}")
    return out
