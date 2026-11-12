#!/bin/bash
# LAN の機械を、測定のあいだ使える状態に保つ（P3）。それぞれ tmux の別のセッションで走らせる。
#   bash scripts/lan_upkeep.sh awake        3060 の機械を寝かせない
#   bash scripts/lan_upkeep.sh tunnel-3060  3060 の LLM（8090）と STT（8091）を、手元の 18090 / 18091 に転送する
#   bash scripts/lan_upkeep.sh tunnel-mbp   MBP の逐次合成の VOICEVOX（50122）を、手元の 50122 に転送する
#   bash scripts/lan_upkeep.sh watch        1 分ごとに 3 つの口を確かめ、続けて止まっていれば起こし直す
# どれも、切れたらつなぎ直す。記録は work/lan/。
#
# 寝かせない仕組み: SSH でつないだ PowerShell から、Windows に SetThreadExecutionState(ES_CONTINUOUS |
# ES_SYSTEM_REQUIRED) で「眠らないで」と頼み続ける。動画の再生ソフトと同じ頼み方で、電源の設定は変えない。
# SSH が切れて PowerShell が終わると、頼みも消える。測定の通信だけでは寝てしまった（2026-11-10 20:20 ごろ）。
set -uo pipefail
WIN=win
WIN_IP=192.168.11.46
WIN_MAC=50:EB:F6:5B:E4:77  # vEthernet (sw1) の MAC。Wake-on-LAN が効くかは未確認
MBP=seijiro@192.168.11.36  # MBP2021（Wi-Fi）
LOG_DIR=work/lan
mkdir -p "$LOG_DIR"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)

stamp() { date "+%Y-%m-%dT%H:%M:%S"; }

PS_AWAKE='$sig = '"'"'[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'"'"'
$k = Add-Type -MemberDefinition $sig -Name Power -Namespace TomokoV4 -PassThru
while ($true) {
  [void]$k::SetThreadExecutionState([uint32]2147483649)
  Write-Output ("awake " + (Get-Date -Format s))
  Start-Sleep -Seconds 60
}'

# VOICEVOX（PR 1823 の版）を MBP で起こし直す。pyworld のビルドが古い道具の残骸で失敗するので、SDK のヘッダーを指す
VV_START='cd ~/Sync/sync_work/by-llms/async-voicevox \
  && export SDKROOT="$(xcrun --show-sdk-path)" \
  && export CPLUS_INCLUDE_PATH="$SDKROOT/usr/include/c++/v1" \
  && mkdir -p ~/tomoko-v4-logs \
  && HOST=127.0.0.1 PORT=50122 nohup ./run_streaming_voicevox.command >> ~/tomoko-v4-logs/voicevox-pr1823.log 2>&1 < /dev/null &'

wake_on_lan() {
  python3 -I - "$WIN_MAC" <<'EOF'
import socket
import sys

mac = bytes.fromhex(sys.argv[1].replace(":", "").replace("-", ""))
packet = b"\xff" * 6 + mac * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for addr in ("255.255.255.255", "192.168.11.255"):
    s.sendto(packet, (addr, 9))
EOF
}

# 3060 の上で、サーバーが返事をするか（転送が切れているだけなのかを見分けるため、向こうで確かめる）
win_code() {
  ssh "${SSH_OPTS[@]}" "$WIN" "curl -s -m 5 -o NUL -w %{http_code} $1" 2>/dev/null || echo ssh-fail
}

case "${1:-}" in
awake)
  encoded=$(printf '%s' "$PS_AWAKE" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')
  while true; do
    echo "$(stamp) つなぐ" >> "$LOG_DIR/awake.log"
    ssh "${SSH_OPTS[@]}" "$WIN" "powershell -NoProfile -EncodedCommand $encoded" >> "$LOG_DIR/awake.log" 2>&1
    echo "$(stamp) 切れた（$?）" >> "$LOG_DIR/awake.log"
    if ! ping -c 1 -t 3 "$WIN_IP" > /dev/null 2>&1; then
      echo "$(stamp) 応答なし。Wake-on-LAN を送る" >> "$LOG_DIR/awake.log"
      wake_on_lan
    fi
    sleep 20
  done ;;
tunnel-3060)
  while true; do
    ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -N -L 18090:127.0.0.1:8090 -L 18091:127.0.0.1:8091 "$WIN"
    echo "$(stamp) tunnel-3060 切れた（$?）" >> "$LOG_DIR/tunnel.log"
    sleep 5
  done ;;
tunnel-mbp)
  while true; do
    ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -N -L 50122:127.0.0.1:50122 "$MBP"
    echo "$(stamp) tunnel-mbp 切れた（$?）" >> "$LOG_DIR/tunnel.log"
    sleep 5
  done ;;
watch)
  down_llm=0 down_stt=0 down_vv=0
  while true; do
    llm=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:18090/health)
    stt=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:18091/)
    vv=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:50122/version)
    batt=$(ssh "${SSH_OPTS[@]}" "$MBP" "pmset -g batt | grep -o '[0-9]*%; [^;]*'" 2>/dev/null)
    echo "$(stamp) llm=$llm stt=$stt voicevox=$vv mbp_battery=[$batt]" >> "$LOG_DIR/watch.log"
    # 手元から見えないときだけ、向こうで確かめる。3 回続けて止まっていたら起こし直す（読み込み中に二重に起こさない）
    if [ "$llm" != 200 ] && [ "$(win_code http://127.0.0.1:8090/health)" != 200 ]; then
      down_llm=$((down_llm + 1))
    else
      down_llm=0
    fi
    if [ "$stt" != 200 ] && [ "$(win_code http://127.0.0.1:8091/)" != 200 ]; then
      down_stt=$((down_stt + 1))
    else
      down_stt=0
    fi
    if [ "$vv" != 200 ] && ! ssh "${SSH_OPTS[@]}" "$MBP" "curl -s -m 5 http://127.0.0.1:50122/version" > /dev/null 2>&1; then
      down_vv=$((down_vv + 1))
    else
      down_vv=0
    fi
    if [ "$down_llm" -ge 3 ]; then
      echo "$(stamp) LLM を起こし直す" >> "$LOG_DIR/watch.log"
      ssh "${SSH_OPTS[@]}" "$WIN" "schtasks /run /tn v3-llama-e4b" >> "$LOG_DIR/watch.log" 2>&1
      down_llm=0
    fi
    if [ "$down_stt" -ge 3 ]; then
      echo "$(stamp) STT を起こし直す" >> "$LOG_DIR/watch.log"
      ssh "${SSH_OPTS[@]}" "$WIN" "schtasks /run /tn v3-whisper" >> "$LOG_DIR/watch.log" 2>&1
      down_stt=0
    fi
    if [ "$down_vv" -ge 3 ]; then
      echo "$(stamp) VOICEVOX を起こし直す" >> "$LOG_DIR/watch.log"
      ssh "${SSH_OPTS[@]}" "$MBP" "zsh -lic 'bash -s'" <<< "$VV_START" >> "$LOG_DIR/watch.log" 2>&1
      down_vv=0
    fi
    sleep 60
  done ;;
*)
  echo "使い方: bash scripts/lan_upkeep.sh awake|tunnel-3060|tunnel-mbp|watch" >&2
  exit 2 ;;
esac
