# 2026-09-11 待发布成片核查

本次核查起点为 **34 条账面待发布成片**，逐条比对了实际交付元数据、文件指纹、生成版本和 B站发布回执。这里不把历史已发布稿件算作待发布库存。

| 处理结果 | 条数 |
|---|---:|
| 原本已使用新版字幕和包装规则 | 11 |
| 旧版已重新生成视频、字幕、片内标题和封面，并通过复检 | 9 |
| 重复内容，已排除出发布队列 | 4 |
| 旧转写或选段不合格，暂缓发布 | 8 |
| 母片文件指纹不匹配，升级阻断并暂停发布 | 2 |
| 合计 | 34 |

本批整理后保留 **20 条可用成片**。这是文件库存数量，不代表当天发布配额；真人视频与音频卡的发布比例仍由现有排期规则控制。

## 已完成重做的旧版成片

| 原任务 | 原片序号 | 新任务 |
|---|---|---|
| #659 | 第3条 | [#734](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34574027961) |
| #667 | 第1条 | [#737](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34574645617) |
| #668 | 第1条 | [#736](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34574644052) |
| #669 | 第2条 | [#731](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34573461789) |
| #674 | 第1、2条 | [#741](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34574913918) |
| #695 | 第1条 | [#727](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34572634883) |
| #697 | 第2条 | [#724](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34572162748) |
| #701 | 第1条 | [#723](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34571809307) |

旧版副本已暂停进入发布队列；原文件和历史发布回执保留。本次未重新投稿已发布稿件，发布回执继续用于防重。

## 暂缓与阻断

- **内容质量暂缓8条**：#650第2、4条；#656第1、2条；#659第2条；#671第1条；#679第1条；#697第1条。逐条原因见 JSON 清单，主要为跨话题、结尾回答不完整或旧 ASR 明显错误。这些片需要重新转写或重新选段，不能只更换字幕样式。
- **母片阻断2条**：#663第1、2条。本次重做任务[#738](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/34574674814)下载得到的母片文件指纹与旧转写绑定的文件指纹不同。时长和分辨率相同不足以放行旧转写，因此保持暂停状态。
- **重复4条**：#662第1条与已发布内容重复；#669第1条、#650第3条、#661第1条与其他库存重复或源区间重叠。

## 核验材料

- [逐条版本及处理清单](stock-upgrade-0911-review.json)
- [固定重做区间、暂停原因与母片阻断证据](stock-upgrade-0911.json)
- [最终实际库存核查](stock-upgrade-0911-results.json)

本次还修复了旧标题误用、静态音频卡被强制转换成真人跟踪视频、柜门把手被误判为文字角标，以及 FC 整份旧状态覆盖并发入库结果的问题。修复保留发布回执、原片指纹和现有质量门禁；未将暂缓项标为成功。
