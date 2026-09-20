# 本地林园工程

此工程现在可以在本地进行代码修改、离线回归和封面排版预览。生产仍由 GitHub Actions 与 FC 执行。

## 本机已准备的环境

- 完整 Git checkout：`/Users/liwenbo/codex-test/bilingual-subtitle-burner`
- 本轮分支：`codex/linyuan-reliability-packaging`
- 基线：`3563b92b2c34bdc2d395d9b4ac03b161c5971fae`
- Python 3.11.16，虚拟环境 `.venv311`；依赖见 `requirements-dev.txt`。
- 输出和对标图片只放 `output/optimization-v1/`，已被 Git 忽略。

```bash
cd /Users/liwenbo/codex-test/bilingual-subtitle-burner
bash scripts/check_linyuan_local.sh
.venv311/bin/python scripts/preview_linyuan_iteration.py
open output/optimization-v1/index.html
```

回归不调用付费模型、不触发云端生产、不投稿。封面预览使用工程已留存的肖像与同一条历史文案，是排版比较，不是新成片验收。新下载的参考账号封面只用于对照，不作为我们的生产素材。

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

开发依赖不含 ffmpeg/ffprobe、Ollama、语音模型、OCR 模型或原始母片。本轮没有下载多 GB 的文本/语音模型，也没有在本机执行完整 ASR—渲染—发布链路。

生产环境准备仍以 `linyuan/ASR_OFFLINE.md` 和 `linyuan/asr_production_config.json` 为准。本文不把“能跑测试”写成“能稳定出片”。本地与线上标题默认模型均为 `qwen3:8b`；显式更换模型时应重新执行真实字幕批测，不能复用旧模型的审核结论。

## 本轮策略与兼容

- 标题仍生成三个角度，但实际合格的一至三个候选都能送独立复核；事实检查一项也不减少。
- 同分选稿记录在 `editorial_selection`，它是选择依据，不是点击率预测。
- `COVER_STYLE=auto`：合格现场原画优先；现场构图不合适时复用已核验帧生成 `editorial` 文字版；再失败才走原有参考人物图回退。显式 `scene` 保持严格模式。
- `COVER_STYLE=editorial/photo/light/dark` 可显式选择。舞台专用分支仍使用原来的经过验证的封面路径。
- 来源历史只小幅影响顺序：30天内至少三个不同母片的明确结果才生效，重试不重复计数，服务故障不惩罚素材来源，不封禁未知来源。
- 普通分片的耗时、失败分类、可重试分片编号加入 `batch_report.json`；这版没有把部分失败自动重新投稿，也不修改配额。
- 时长门槛暂时仍为120秒；90秒短观点策略需要同步生产和投稿端，不能只改选段器。

## 上线前验收

先用固定真实字幕重复生成，记录标题通过/兜底/失败与耗时；然后用固定母片实产，对照实际画面、声音和字幕。最后才考虑合并、FC 部署与线上观察。线上恢复与发布流程见 `linyuan/fc/README.md`，以真实成片和投稿回执为准。
