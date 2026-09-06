林园中文 ASR 的固定约束：CPU、本地模型、本地解码，不调用识别 API，不使用 Whisper large-v3 作为升级或自动回退候选。模型与 Python 依赖在准备阶段下载好，再复制到离线机器。现有视频审核、选段和翻译是独立步骤，本次约束与验收覆盖语音识别和转写清理。

**当前生产配置**

- 后端：SenseVoice Small INT8，sherpa-onnx 1.13.7 CPU，默认 2 线程。
- 解码：30 秒核心区，前后各 3 秒上下文；先按真实 token 时间戳确定归属，再统一组句。
- 保真：停止通过文本 API 改写转写结果；SenseVoice 不再套用自回归模型的循环删除和模糊去重规则。数字、否定和真实重复不会被这些后处理规则吞掉。
- 缓存：核对音视频 SHA256、模型 SHA256、配置、术语表、处理版本与字幕文件校验和。失配时归档旧字幕及依赖它的选段、翻译、文案、意群缓存，重新识别。
- 留证：`asr_raw_chunks.json` 保留每段原始输出；`asr_tokens.json` 保留 token 与时间戳；`cues_raw.json` 是兼容既有生产接口的组句结果；`asr_cache.json` 记录来源。
- 句末标点保留在转写结果中，显示时再处理。成片继续执行原来的完整意群、人物、黑边、二维码等质量门禁。

**在已有本地模型上运行识别**

准备 Python 依赖 `numpy sherpa-onnx` 与系统 `ffmpeg`；模型目录应含 `model.int8.onnx` 和 `tokens.txt`。在仓库根目录运行：

```bash
ASR_BACKEND=sensevoice SENSEVOICE_MODEL_DIR=/absolute/sensevoice_model ASR_CPU_THREADS=2 python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'linyuan')
from produce_cn import transcribe
cues = transcribe(Path('input.mp4'), Path('out/offline-asr'))
print(''.join(c['text'] for c in cues))
PY
```

本步骤不需要 API key。SenseVoice 模型来自 [sherpa 官方发布](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)。

**真实音频 CPU 横评**

测试为 12 段林园生产音频，共 275.92 秒；AMD EPYC 7763 的 GitHub runner、4 个可见逻辑核，识别限定 2 线程。模型准备下载不计入识别耗时；识别过程关闭 Python 网络连接。首轮结果如下：

| 后端 | 12 段识别耗时 | 实时率 RTF | 进程峰值内存 | 本次返回时间戳 |
|---|---:|---:|---:|---|
| SenseVoice INT8 | 12.50 秒 | 0.045 | 440 MiB | 12/12 |
| Paraformer INT8 | 9.43 秒 | 0.034 | 426 MiB | 0/12 |
| Qwen3-ASR 0.6B FP32 | 156.05 秒 | 0.566 | 6102 MiB | 0/12，未加独立对齐模型 |

没有人工逐字听写的标准答案，因此不报告 CER、准确率或“提升百分比”。Qwen 的个别口语更通顺，难词仍出现错误；Paraformer 的速度优势也不能代替识别质量和时间轴验收。先保留经过完整链路验证的 SenseVoice，候选模型不自动进入生产。

可用 `linyuan/asr_cpu_benchmark.py` 复现离线模型对比；`--context-file linyuan/asr_finance_vocabulary.json` 用于 Qwen 的财经词表实验。测试输出保留完整假设文本、音频哈希、CPU 信息、耗时、内存及依赖版本。词表只作为候选模型上下文，不强制向字幕加入词表词。

[首轮 Qwen 记录](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34026825959)；[SenseVoice 与 Paraformer 记录](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34026954219)；[完整音频验收](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34027255724)。Qwen 模型与本地加载方法见 [官方仓库](https://github.com/QwenLM/Qwen3-ASR)；Paraformer 导出模型的时间戳限制见 [sherpa 官方说明](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-paraformer/paraformer-models.html)。
