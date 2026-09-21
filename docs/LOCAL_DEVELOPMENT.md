# 本地林园工程

此工程现在可以在本地进行代码修改、离线回归和封面排版预览。生产仍由 GitHub Actions 与 FC 执行。

## 本机已准备的环境

- 完整 Git checkout：`/Users/liwenbo/codex-test/bilingual-subtitle-burner`
- 本轮分支：`codex/linyuan-reliability-packaging`
- 原始开发基线：`3563b92b2c34bdc2d395d9b4ac03b161c5971fae`；固定100对照使用更新的主线 `1c662a1`，版本和规则见下方对照文档。
- Python 3.11.16，虚拟环境 `.venv311`；依赖见 `requirements-dev.txt`。
- 输出和参考证据位于 `output/optimization-v1/`、`output/benchmark-20260921/`、`output/baseline-comparison-20260921/`，已被 Git 忽略。

```bash
cd /Users/liwenbo/codex-test/bilingual-subtitle-burner
bash scripts/check_linyuan_local.sh
.venv311/bin/python scripts/preview_linyuan_iteration.py
open output/optimization-v1/index.html
.venv311/bin/python scripts/build_linyuan_comparison_page.py
open output/benchmark-20260921/comparison.html
```

回归不调用付费模型、不触发云端生产、不投稿。页面会读取本轮已下载的完整样片和字幕对照；单独重建脚本不会下载这些产物。封面排版演示使用已留存肖像和同一条历史文案，与真实样片分开展示。新下载的参考账号封面只用于对照，不作为我们的生产素材。

## 在其他机器重建

使用 Python 3.11 创建虚拟环境，再安装开发依赖：

```bash
python3.11 -m venv .venv311
.venv311/bin/python -m pip install -r requirements-dev.txt
bash scripts/check_linyuan_local.sh
```

Linux 还需要安装 Noto Sans CJK 中文字体来执行实际封面渲染检查。macOS 自动选择 PingFang SC Semibold；可设置 `COVER_FONT_PATH`。没有中文字体时封面测试会明确跳过，不能当作已验证视觉效果。

OpenCV 在本机首次加载耗时较长；这不代表出片程序已经运行。本轮最终完成了含 OpenCV 的回归，没有通过移除画面检查来获得通过结果。

## 真正出片还需要什么

开发依赖现在包含 `imageio-ffmpeg` 提供的 FFmpeg。没有系统 FFmpeg 时，检查脚本只在被忽略的 `output/local-tools/` 建立可执行链接并对本次进程生效。它不包含 ffprobe、Ollama、语音模型、OCR 模型或原始母片；本机尚未执行完整 ASR—渲染—发布链路。

生产环境准备仍以 `linyuan/ASR_OFFLINE.md` 和 `linyuan/asr_production_config.json` 为准。本文不把“能跑测试”写成“能稳定出片”。本地与线上标题默认模型均为 `qwen3:8b`；显式更换模型时应重新执行真实字幕批测，不能复用旧模型的审核结论。

## 本轮策略与兼容

- 标题仍生成三个角度，但实际合格的一至三个候选都能送独立复核；事实检查一项也不减少。
- 同分选稿记录在 `editorial_selection`，它是选择依据，不是点击率预测。
- `COVER_STYLE=auto`：合格现场原画优先；现场构图不合适时复用已核验帧生成 `editorial` 文字版；再失败才走原有参考人物图回退。显式 `scene` 保持严格模式。
- `COVER_STYLE=editorial/photo/light/dark` 可显式选择。舞台专用分支仍使用原来的经过验证的封面路径。
- 真人动态窗口也优先从已验画面选封面，不因内部使用卡片合成而固定使用资料照。`LIVE_CARD_THEME=contrast/light` 选择真人竖卡的黑底或浅色；默认黑底，窗口与字幕几何不变。
- 来源历史只小幅影响顺序：30天内至少三个不同母片的明确结果才生效，重试不重复计数，服务故障不惩罚素材来源，不封禁未知来源。
- 普通分片的耗时、失败分类、可重试分片编号加入 `batch_report.json`；这版没有把部分失败自动重新投稿，也不修改配额。
- `SUBTITLE_LAYOUT=footer`：普通原画视频的实验字幕带，追加画布高度并保留原画；不改变默认字幕。舞台专用与纯音频路径不在本轮实测范围内。
- 时长门槛暂时仍为120秒；90秒短观点策略需要同步生产和投稿端，不能只改选段器。

## 上线前验收

先用固定真实字幕重复生成，记录标题通过/兜底/失败与耗时；然后用固定母片实产，对照实际画面、声音和字幕。最后才考虑合并、FC 部署与线上观察。线上恢复与发布流程见 `linyuan/fc/README.md`，以真实成片和投稿回执为准。

## 已下载的真实验收产物

`output/optimization-v1/index.html` 可直接用浏览器打开，包含：

- `fixed-review/10608098788/final.mp4`：固定母片生成的 267.7 秒完整样片，另有封面及 30 秒预览。[运行记录](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35520397101)。
- `caption-footer-preview/before.mp4` 与 `footer.mp4`：同字幕、同时间的真实 30 秒布局比较。[运行记录](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35521088500)。
- `title-summary/` 与 `title-guard-summary/`：首批 18 次、追加 6 次的独立汇总，不将两次代码版本混算。

上述目录被 Git 忽略，换机器时需从对应 Actions artifacts 恢复，不能只靠 clone 得到视频。精简的最终标题、证据、代码指纹和复核备注已提交至 `linyuan/simulations/title-batch-20260920/optimization-review.json`；原始完整模型调用保存在 artifacts。

2026-09-21本机离线检查为342 passed，已包含真实编码、黑边范围和动态取景回归；不再因缺少FFmpeg跳过这些检查。真实云端编码通过并不代表本机已具备全部生产依赖。历史结论见 [首轮验收报告](LINYUAN_OPTIMIZATION_2026-09-20.md)，最新100素材与20参考对照见 [主线比较](LINYUAN_MAIN_COMPARISON_2026-09-21.md)。

生产默认 `LINYUAN_CONTENT_POLICY=reference_v1`，按参考内容允许20秒起的完整连续观点；旧规则可显式设 `legacy120`。历史测试夹具显式沿用旧规则，新策略测试在独立进程校验默认值、源片/选段/最终MP4时长及新旧库存隔离。固定100 A/B工作流仍显式使用旧规则。
