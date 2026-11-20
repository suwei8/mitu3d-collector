# mitu3d-collector

福彩3D 数据采集器。定时运行（北京时间 12:00 / 18:25 / 18:55 / 19:35 / 21:30 / 22:10），
从天齐网、彩之家、牛彩网等公开来源采集试机号与字谜数据。

本仓库只存放采集代码与调度工作流，**不存储数据**。采集结果由 GitHub Actions
通过 deploy key 推送至私有归档仓库 `suwei8/mitu3d-archive`。

## 本地运行

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest -q
python scripts/collect.py            # 采集最新期（写入本仓库 data/ raw/）
```

将 `FC3D_ROOT` 指向归档仓库的本地 checkout，即可把采集结果写入归档：

```bash
FC3D_ROOT=/path/to/mitu3d-archive python scripts/collect.py
```

## 信封快报图像主线（scripts/xinfeng.py）

「虎头信封图」的上游是「中国福彩3D信封快报」产品族，存在两个版式同构、
数值独立的公开版本，本主线分别独立采集：

- `cz89`（牛彩图谜版，升序）：`/3d/item_3.htm` 图谜列表按期号发现文章
  （主源 `m.cz89.com`，镜像 `m.01p.net`），文章页图床 `pic.cz89.com`；
- `e46`（彩吧 E46「北京3D信封快报·打黑版」，乱序）：页面 URL 按规则直拼
  `https://m.55125.cn/tuku/{期号}/E46.htm`，图床 `tuku.55123.cn` 存在死 DNS IP，
  直连失败自动 `--resolve` 到 `E46_RESOLVE_IP`。

字段只存在于 JPG 像素里，不走通用 OCR：固定版式格位裁切 → 字形归一化
44×32 → 与人工核验模板库（`scripts/xinfeng_templates.json`，由
`scripts/build_xinfeng_templates.py` 重建）最近邻比对，差分超过
`_ACCEPT_SCORE` 的格位标 `?` 并置 `ocr_ok=false`（`worst_score` 留档供复核）。

提取字段：七码复试 `seven`、六码复试 `six`、精选五码 `five`、胆码 `dan`。
原图始终存档到 `images/YYYY/期号/xinfeng-{cz89,e46}.jpg`，与抽取结果一起入档；
识别不完整时 Telegram 推送附「（部分待复核）」。

单源失败只记 warn 不拖垮整批；当期文章未发布时返回空、由下一轮定时任务重试。
已确认产品族之外的群图字段（邮票两胆/信封三胆/印字四码/尾参/形态%）
未定位公开来源，不采集、不猜填（见归档仓 `analysis/provenance-hutou-xinfeng.md`）。

## Telegram 推送

归档仓库有数据变更时，自动把最新一期记录推送到 Telegram。
需要在仓库 Settings → Secrets and variables → Actions 配置：

- `TELEGRAM_BOT_TOKEN`：BotFather 颁发的 bot token
- `TELEGRAM_CHAT_ID`：接收消息的 chat id

平时仅在归档有数据变更时推送；手动触发可勾选 `test_notify` 强制推一条
（用于验证 secrets 配置）。

## 手动触发

Actions → collect → Run workflow，可选参数：

- `issue`：采集指定期号
- `next_issue`：午间预告——采集上游最新期+1（信封快报两版前夜已上架），
  未发布则跳过不写记录；常与 `test_notify` 同勾，保证午间必有推送
- `from_issue` / `to_issue`：区间回填
