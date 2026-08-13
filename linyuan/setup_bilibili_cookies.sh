#!/usr/bin/env bash
#
# 一键把 B站 cookies 写进仓库 secret（macOS）
#
# 用法:
#   ./setup_bilibili_cookies.sh                    # 先扫码登录再写入（一条龙）
#   ./setup_bilibili_cookies.sh --file cookies.json  # 用已有的 cookies.json
#
# 与 setup_youtube_cookies.sh 的差异：
#   YouTube 版用 yt-dlp 从浏览器抽 cookie；B站不需要 —— bili_login.py 自己调
#   passport 接口拿二维码、轮询扫码、直接产出 biliup 认的 cookies.json。
#
# 安全规则（与 YouTube 版一致）：
#   - 只打印条数、字节数这类聚合信息，cookie 值绝不出现在输出里
#   - 写 secret 走 stdin，不进命令行参数（会进 ps 和 shell history）
#
set -uo pipefail

REPO="fastisrealslow/bilingual-subtitle-burner"
SECRET="BILIBILI_COOKIES"
LIMIT=48000  # GitHub 单个 secret 上限 48 KB
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# ---------- 参数 ----------
SRC_FILE=""
if [ "${1:-}" = "--file" ]; then
  [ -n "${2:-}" ] || die "--file 后面要跟文件路径"
  SRC_FILE="$2"
fi

# ---------- 1. 依赖 ----------
step "检查依赖"

command -v gh >/dev/null 2>&1 || die "没装 gh。brew install gh 之后再来。"
ok "gh $(gh --version | head -1 | awk '{print $3}')"

command -v python3 >/dev/null 2>&1 || die "没装 python3"
ok "python3 $(python3 --version | awk '{print $2}')"

# ---------- 2. gh 登录与权限 ----------
step "检查 GitHub 登录"

gh auth status >/dev/null 2>&1 || die "gh 没登录。跑 'gh auth login' 之后再来。"
WHO="$(gh api user --jq .login 2>/dev/null)"
[ -n "$WHO" ] || die "取不到 GitHub 身份，重新跑 'gh auth login'"
ok "已登录: $WHO"

gh api "repos/$REPO" --jq '.permissions.admin' 2>/dev/null | grep -q true \
  || die "当前账号对 $REPO 没有管理权限，改不了 secret。"
ok "对 $REPO 有管理权限"

# ---------- 3. 取 cookies ----------
if [ -n "$SRC_FILE" ]; then
  step "读取已有文件"
  [ -f "$SRC_FILE" ] || die "文件不存在: $SRC_FILE"
  COOKIES_JSON="$SRC_FILE"
  ok "读到 $(wc -c <"$COOKIES_JSON" | tr -d ' ') 字节"
else
  step "扫码登录（产出 cookies.json）"
  echo "  用 B站 App 扫接下来出现的二维码。"
  echo "  ⚠️  建议用专门投稿的小号扫 —— 登录态会写进 CI。"
  echo
  (cd "$SCRIPT_DIR" && python3 bili_login.py) \
    || die "扫码登录失败。重跑一次，或检查网络。"
  COOKIES_JSON="$SCRIPT_DIR/cookies.json"
  [ -f "$COOKIES_JSON" ] || die "登录跑完了但没找到 $COOKIES_JSON"
  ok "登录成功，cookies.json 已生成"
fi

# ---------- 4. 校验 ----------
step "校验"

python3 - "$COOKIES_JSON" "$LIMIT" <<'PY' || exit 1
import json, sys

path, limit = sys.argv[1], int(sys.argv[2])
raw = open(path, "rb").read()

if len(raw) >= limit:
    print(f"✗ {len(raw)} 字节，超过 GitHub 48 KB 上限", file=sys.stderr)
    sys.exit(1)
print(f"✓ 体积合规（{len(raw)} 字节 / 上限 {limit}）")

try:
    d = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"✗ 不是合法 JSON：第 {e.lineno} 行", file=sys.stderr)
    sys.exit(1)

# biliup 认的格式：{"cookies": [{"name": ..., "value": ...}, ...]}
entries = d.get("cookies") if isinstance(d, dict) else None
if not isinstance(entries, list):
    # 兼容裸 {name: value} 字典格式
    if isinstance(d, dict) and "SESSDATA" in d:
        entries = [{"name": k} for k in d]
    else:
        print("✗ 认不出格式：既不是 biliup 的 {cookies:[...]} 也不是 {name:value} 字典",
              file=sys.stderr)
        sys.exit(1)

names = {e.get("name") for e in entries if isinstance(e, dict)}
print(f"✓ 记录 {len(entries)} 条")

missing = {"SESSDATA", "bili_jct", "DedeUserID"} - names
if missing:
    print(f"✗ 缺关键 cookie：{sorted(missing)} —— 这是登录态的三件套，"
          f"缺了说明导出的是未登录状态", file=sys.stderr)
    sys.exit(1)
print("✓ 含 SESSDATA / bili_jct / DedeUserID（登录态完整）")
PY

[ $? -eq 0 ] || die "校验未过"

# ---------- 5. 写入 ----------
step "写进仓库 secret"

gh secret set "$SECRET" --repo "$REPO" < "$COOKIES_JSON" || die "写入失败"

if gh secret list --repo "$REPO" | grep -q "^$SECRET"; then
  ok "$SECRET 已写入 $REPO"
else
  die "写入命令没报错，但列表里查不到，需要人工看一下"
fi

step "完成"
cat <<EOF
现在的 secret 列表:
$(gh secret list --repo "$REPO" | sed 's/^/  /')

最后一件事，很关键:
  这份 cookies 对应的 B站会话，之后别在别处的浏览器里退出登录或频繁异地登录。
  凭据被服务端轮换后，CI 里这份跟着失效 —— 到时候重跑一遍本脚本换新即可。

可以触发 linyuan-produce-cn 跑一条试试，出片成功后会自动投稿。
EOF
