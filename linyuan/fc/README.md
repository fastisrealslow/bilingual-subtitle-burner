# 阿里云函数计算部署指引（5 步，约 15 分钟）

林园流水线的境内执行端：选片调度 + B站投稿。
cookies 和 GitHub token **只存在你自己的阿里云账号**，不碰公司设备。

## 1. 开通 + 建函数

1. 登录 [函数计算控制台](https://fcnext.console.aliyun.com/)（个人账号，开通免费）
2. 「函数」→ 创建函数：
   - 运行时：**Python 3.10**
   - 地域：任意（杭州/上海均可）
   - 上传代码：把本目录 `index.py` 打成 zip 上传（或直接粘贴进在线编辑器）
   - 执行超时时间：**600 秒**（调度要下载视频，默认 60s 不够）

## 2. 装依赖层（biliup）

在 Mac 终端执行（注意 `--platform`，FC 是 linux x86_64，不能直接装 Mac 版）：

```bash
mkdir -p /tmp/fc-layer && cd /tmp/fc-layer
pip3 install biliup --platform manylinux2014_x86_64 --only-binary=:all: -t python
zip -qr layer.zip python
```

然后控制台 →「层」→ 创建层 → 上传 `layer.zip` → 回到函数 →「配置」→ 添加该层。

## 3. 配环境变量

函数「配置」→ 环境变量：

| 变量 | 值 |
|---|---|
| `GITHUB_TOKEN` | 你的 GitHub PAT（repo + actions 权限） |
| `BILIBILI_COOKIES` | `cookies.json` 的**全文**（biliup login 产出的那个文件，原样粘贴） |

## 4. 配两个定时触发器

「触发器」→ 创建触发器 → 定时触发器：

| 名称 | Cron 表达式 | 入口 |
|---|---|---|
| 每日调度 | `0 0 10 * * *` | `index.dispatch_handler` |
| 投稿 | `0 30 * * * *` | `index.publish_handler` |

## 5. 手动点一次「测试」验证

先测 `dispatch_handler`：看日志里是否出现「候选 N 条 / 已调度 ly-xxxx」。
再测 `publish_handler`：队列里有成片时会直接投出，日志给 bvid 链接。

---

**状态存哪**：`linyuan/.automation/fc_state.json`（GitHub 仓库里），冷启动不丢进度。

**成本**：函数计算每月免费额度 40 万 GB·秒，这套一天跑几分钟，≈0 元。

## 后续代码自动部署

函数首次建好后，只需在 GitHub 仓库的 `Settings → Secrets and variables → Actions`
一次性添加 `ALIYUN_AK` 和 `ALIYUN_SK`。此后 `linyuan/fc/index.py` 进入 `main`
会由 `FC production deploy` workflow 自动更新函数代码，不再手工上传 ZIP。

自动部署只更新代码包，FC 中已有的 `GITHUB_TOKEN`、`BILIBILI_COOKIES`、
依赖层、超时和触发器都会保留。若函数不在杭州或名称不是 `fc-develop`，
再添加仓库 Variables：`FC_REGION`、`FC_FUNCTION_NAME`。
