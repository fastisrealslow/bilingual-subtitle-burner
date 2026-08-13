# 无浏览器改造记录

目标：脱离 CDP 浏览器（登录态）运行，以便部署成服务。

## 结论

| 源 | 改造前 | 改造后 | 方案 |
|----|--------|--------|------|
| B站 | 需浏览器 | ✅ 纯 HTTP | 搜索 API `api.bilibili.com/x/web-interface/wbi/search/type`，无需登录 |
| 腾讯新闻 | 需浏览器 | ✅ 纯 HTTP | 作者流 `i.news.qq.com/getSubNewsMixedList` + `getWebVideo` |
| 好看视频 | 已是纯 HTTP | ✅ | 播放页正则提 MP4 |
| 微博 | 需浏览器 | ✅ 纯 HTTP | passport 访客票据 + `weibo.com/ajax/statuses/search` |
| 抖音 | 需浏览器 | ✅ 纯 HTTP | iesdouyin 分享页 `_ROUTER_DATA` |
| 雪球 | 需浏览器 | ❌ 仍需 | WAF 挑战值需执行 JS 计算 |

**5/6 可无浏览器运行。** 脚本通过 `cdp_available()` 自动探测：
有浏览器跑全量，无浏览器自动跳过 `BROWSER_REQUIRED` 中的源。

## 关键发现

### 微博：m 站拒绝访客票据，PC 站接受
- `m.weibo.cn/api/container/getIndex` → `ok=-100`（票据不被接受）或 HTTP 432（无 cookie）
- `weibo.com/ajax/statuses/search` → ✅ 正常返回

流程：`passport.weibo.com/visitor/genvisitor` 拿 tid
→ `incarnate` 换 SUB/SUBP 票据 → 访问 `weibo.com/` 激活
→ 带 `XSRF-TOKEN` 请求搜索接口。

⚠️ 全程必须使用同一个 UA，中途切换会失效。

效果：13 条 → **70 条**，26 条带 720P 视频直链。

### 抖音：主站有反爬，分享页没有
- `www.douyin.com/video/xxx` → 验证码
- yt-dlp → 要求 `__ac_nonce`（需执行反爬 JS，纯 HTTP 拿不到；
  已试 ttwid 注册接口、msToken 本地生成，均不足）
- `www.iesdouyin.com/share/video/{vid}/` → ✅ 无防护

从页面 `_ROUTER_DATA` 取 `loaderData[*].videoInfoRes.item_list[0]`，
其中 `video.play_addr.url_list[0]` 把 `/playwm/` 替换为 `/play/` 即**无水印**直链。
额外附带真实发布时间、点赞数、评论数。

⚠️ **有 IP 级限流**：批量密集请求会导致 `_ROUTER_DATA` 消失。
应对措施：
- 已入库且有直链的视频跳过（`refresh_days` 默认 7 天刷新一次）
- 单条间隔 2 秒，失败重试退避 2.5s/5s
- 连续失败 5 次提前停止，留待下轮

### 雪球：未攻克
`xueqiu.com/query/v1/search/status.json` 返回 WAF 挑战：
```json
{"_waf_bd8ce2ce37": "<每次不同的值>"}
```
回填 cookie 后仍被拦，挑战值需执行 JS 计算。保留浏览器依赖。
影响小：雪球是纯文本源，目前仅 9 条。

## 数据变化

改造前 195 条 → 改造后 **293 条**（微博增长贡献最大）。

## 同名干扰过滤

微博搜索会混入「东北虎林园」「华林园景区」「橘子林园互评群」等无关内容。
`WeiboSearchSource` 内置规则过滤：命中噪声词且无投资信号词则丢弃，
实测每轮过滤约 25 条。
