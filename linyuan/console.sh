#!/usr/bin/env bash
# 控制台服务管理
#
# 为什么要 setsid：直接 nohup 起的进程会随父 shell 会话一起被回收，
# 必须完全脱离会话才能常驻。
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8420}"
LAN_IP=$(ip -4 route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
LAN_IP=${LAN_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}
LAN_IP=${LAN_IP:-127.0.0.1}
PIDFILE=".console.pid"
LOGFILE=".console.log"

running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null
}

case "${1:-status}" in
  start)
    if running; then echo "已在运行 (pid $(cat $PIDFILE))"; exit 0; fi
    setsid nohup python3 server.py --port "$PORT" > "$LOGFILE" 2>&1 < /dev/null &
    sleep 2
    pid=$(pgrep -f "python3 server.py --port $PORT" | head -1)
    if [ -n "$pid" ]; then
      echo "$pid" > "$PIDFILE"
      echo "✅ 已启动 (pid $pid)"
      echo "   局域网  http://$LAN_IP:$PORT/"
      echo "   本机    http://127.0.0.1:$PORT/"
    else
      echo "❌ 启动失败，看 $LOGFILE"; exit 1
    fi
    ;;
  stop)
    if running; then kill "$(cat $PIDFILE)" && rm -f "$PIDFILE" && echo "已停止"
    else echo "未在运行"; rm -f "$PIDFILE"; fi
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    if running; then
      echo "✅ 运行中 (pid $(cat $PIDFILE))  http://$LAN_IP:$PORT/"
    else
      echo "○ 未运行"
    fi
    ;;
  log) tail -f "$LOGFILE" ;;
  *) echo "用法: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
