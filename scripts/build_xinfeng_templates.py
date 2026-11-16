#!/usr/bin/env python3
"""重建 xinfeng_templates.json：从人工核验过的历史期样本提取字形模板。

用法：
    python scripts/build_xinfeng_templates.py SAMPLES_JSON [OUT_JSON]

SAMPLES_JSON 格式：
    [{"image": "path/to/img.jpg", "layout": "cz89"|"e46",
      "fields": {"seven": "0234678", "six": "013678", "five": "01678", "dan": "670"}}, ...]

只收"提取字数与标注一致"的字段；空白字形自动剔除。输出 hex 位图打包的 JSON。
样本留在本仓库之外的归档/本地目录即可——构建只在需要扩库时手动跑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.xinfeng import CANVAS_H, CANVAS_W, cell_glyphs, circle_glyphs, _normalize  # noqa: E402


def pack(arr) -> str:
    bits = "".join(str(int(v)) for v in arr.flatten())
    return format(int(bits, 2), "x")


def main() -> int:
    samples = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent / "xinfeng_templates.json"

    import numpy as np
    from PIL import Image

    lib: dict[str, dict[str, None]] = {}
    skipped = []
    for s in samples:
        im = Image.open(s["image"])
        gray = np.array(im.convert("L"))
        rgb = np.array(im.convert("RGB"))
        h, w = gray.shape
        layout_key = s["layout"]
        from scripts.xinfeng import LAYOUTS
        layout = LAYOUTS[layout_key]
        for row in ("seven", "six", "five"):
            truth = s["fields"][row]
            glyphs = cell_glyphs(gray, layout, row)
            if len(glyphs) != len(truth) or any(g is None for g in glyphs):
                skipped.append(f"{s['image']}:{row} ({sum(g is not None for g in glyphs)}/{len(truth)})")
                continue
            for g, d in zip(glyphs, truth):
                n = _normalize(g)
                if n.sum() < 30:
                    skipped.append(f"{s['image']}:{row} empty glyph for {d}")
                    continue
                lib.setdefault(d, {})[pack(n)] = None
        cx0, cy0, cx1, cy1 = layout["circles"]
        dans = circle_glyphs(rgb, int(w * cx0), int(h * cy0), int(w * cx1), int(h * cy1))
        truth = s["fields"]["dan"]
        if len(dans) != len(truth):
            skipped.append(f"{s['image']}:dan ({len(dans)}/{len(truth)})")
        else:
            for g, d in zip(dans, truth):
                n = _normalize(g)
                if n.sum() < 30:
                    skipped.append(f"{s['image']}:dan empty glyph for {d}")
                    continue
                lib.setdefault(d, {})[pack(n)] = None

    data = {
        "canvas": [CANVAS_H, CANVAS_W],
        "templates": {d: sorted(v.keys()) for d, v in sorted(lib.items())},
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"templates: {{ {', '.join(f'{d}:{len(v)}' for d, v in sorted(data['templates'].items()))} }}")
    for s in skipped:
        print(f"[skip] {s}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
