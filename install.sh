#!/usr/bin/env bash
set -euo pipefail

# install.sh - mjjbox 自动签到 安装 / 卸载 脚本（下载 checkin.py）
# - 默认以 root 运行（脚本会检查）
# - 自动安装系统依赖（可选）
# - 创建 venv 并安装 requests, beautifulsoup4
# - 自动从远程下载 checkin.py（默认 URL 可修改）
# - 写入明文 credentials.conf（username/password/serverchan）
# - 创建 systemd timer（每天）与开机运行 service
#
# 使用：
#   保存为 install.sh
#   chmod +x install.sh
#   su -    # 切换到 root
#   ./install.sh

DEFAULT_DIR="/opt/mjjbox_checkin"
DEFAULT_CHECKIN_URL="https://raw.githubusercontent.com/liaox321/mjjbox-checkin/main/checkin.py"
SERVICE_NAME="mjjbox-checkin.service"
TIMER_NAME="mjjbox-checkin.timer"
BOOT_SERVICE_NAME="mjjbox-checkin-at-boot.service"

# ---------- root check ----------
if [ "$(id -u)" -ne 0 ]; then
  echo -e "\033[1;31m[ERROR]\033[0m 本脚本必须以 root 身份运行。请用 su - 切换到 root 或使用 sudo -i 后再运行。"
  exit 1
fi

echoinfo(){ echo -e "\033[1;34m[INFO]\033[0m $*"; }
echowarn(){ echo -e "\033[1;33m[WARN]\033[0m $*"; }
echoerr(){ echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman"
  elif command -v apk >/dev/null 2>&1; then
    echo "apk"
  elif command -v zypper >/dev/null 2>&1; then
    echo "zypper"
  else
    echo ""
  fi
}

install_system_deps() {
  PKG=$(detect_pkg_mgr)
  if [ -z "$PKG" ]; then
    echowarn "未检测到支持的包管理器（apt/dnf/yum/pacman/apk/zypper）。请手动安装：python3 python3-venv python3-pip curl/wget"
    return 1
  fi

  echoinfo "检测到包管理器: $PKG，开始安装系统依赖（python3/venv/pip/curl/wget）..."
  case "$PKG" in
    apt)
      apt-get update -y
      apt-get install -y python3 python3-venv python3-pip curl wget || return 1
      ;;
    dnf)
      dnf install -y python3 python3-venv python3-pip curl wget || return 1
      ;;
    yum)
      yum install -y python3 python3-venv python3-pip curl wget || return 1
      ;;
    pacman)
      pacman -Sy --noconfirm python python-virtualenv python-pip curl wget || return 1
      ;;
    apk)
      apk add --no-cache python3 py3-virtualenv py3-pip curl wget || return 1
      ;;
    zypper)
      zypper install -y python3 python3-virtualenv python3-pip curl wget || return 1
      ;;
    *)
      echowarn "未知包管理器: $PKG，请手动安装依赖"
      return 1
      ;;
  esac
  echoinfo "系统依赖安装完成（或已存在）"
  return 0
}

show_menu() {
  cat <<EOF
请选择操作:
  1) 安装 (Install)
  2) 卸载 (Uninstall)
  3) 退出 (Exit)
EOF
  read -rp "输入选项数字 [1-3]: " choice
  case "$choice" in
    1) do_install ;;
    2) do_uninstall ;;
    *) echo "退出."; exit 0 ;;
  esac
}

download_checkin_py() {
  local url="$1"
  local dest="$2"
  # prefer curl, fallback to wget
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest" || return 1
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$dest" "$url" || return 1
  else
    return 2
  fi
  chmod +x "$dest"
  return 0
}

do_install() {
  read -rp "安装目录 (默认: ${DEFAULT_DIR}): " INST_DIR
  INST_DIR=${INST_DIR:-$DEFAULT_DIR}

  read -rp "是否自动尝试安装系统依赖（python3/venv/pip/curl/wget）？(Y/n): " AUTO_SYS
  AUTO_SYS=${AUTO_SYS:-Y}

  if [[ "$AUTO_SYS" =~ ^([yY])$ ]]; then
    if ! install_system_deps; then
      echowarn "自动安装系统依赖失败或不完整，请手动安装后再运行本脚本，或选择跳过。"
      read -rp "是否继续（跳过系统依赖安装）？(y/N): " cont
      if [[ ! "$cont" =~ ^([yY])$ ]]; then
        echoinfo "取消安装。"
        exit 1
      fi
    fi
  fi

  # 找 python3
  if command -v python3 >/dev/null 2>&1; then
    PYBIN="$(command -v python3)"
  else
    read -rp "未在 PATH 中找到 python3，请输入 Python 可执行文件路径 (例如 /usr/bin/python3): " PYBIN
  fi

  if [ -z "${PYBIN:-}" ] || [ ! -x "$PYBIN" ]; then
    echoerr "指定的 Python 不存在或不可执行: ${PYBIN:-<空>}"
    exit 1
  fi

  echoinfo "创建安装目录: $INST_DIR"
  mkdir -p "$INST_DIR"
  # try to set owner to the original login user if possible
  OWNER="$(logname 2>/dev/null || echo root)"
  chown "$OWNER:$OWNER" "$INST_DIR" || true

  echoinfo "创建 Python 虚拟环境..."
  "$PYBIN" -m venv "$INST_DIR/venv"
  # shellcheck disable=SC1090
  source "$INST_DIR/venv/bin/activate"

  echoinfo "升级 pip 并安装 python 库 (requests, beautifulsoup4)..."
  pip install --upgrade pip >/dev/null
  pip install requests beautifulsoup4 >/dev/null

  # 获取 checkin.py URL
  read -rp "请输入 checkin.py 的下载 URL (默认: ${DEFAULT_CHECKIN_URL}): " CHECKIN_URL
  CHECKIN_URL=${CHECKIN_URL:-$DEFAULT_CHECKIN_URL}

  echoinfo "从 ${CHECKIN_URL} 下载 checkin.py 到 ${INST_DIR}/checkin.py ..."
  dl_ret=0
  download_checkin_py "$CHECKIN_URL" "${INST_DIR}/checkin.py" || dl_ret=$?
  if [ "$dl_ret" -eq 2 ]; then
    echoerr "系统中既没有 curl 也没有 wget，无法下载 checkin.py。请先安装 curl 或 wget。"
    exit 1
  elif [ "$dl_ret" -ne 0 ]; then
    echoerr "下载 checkin.py 失败（URL: ${CHECKIN_URL}）。请检查 URL 或手动把 checkin.py 放到 ${INST_DIR}/checkin.py"
    exit 1
  fi

  echoinfo "checkin.py 下载并设置为可执行: ${INST_DIR}/checkin.py"

  # 写入凭据文件
  echoinfo "准备写入凭据文件 (明文)：$INST_DIR/credentials.conf"
  read -rp "用户名 (username/email): " USERNAME
  read -s -rp "密码 (注意：将以明文保存): " PASSWORD
  echo
  read -rp "是否启用 Server 酱 通知（填写 key）？(Y/n): " USE_SC
  USE_SC=${USE_SC:-Y}
  SERVERCHAN_KEY=""
  if [[ "$USE_SC" =~ ^([yY])$ ]]; then
    read -rp "请输入 serverchan key（回车留空表示不启用）: " SERVERCHAN_KEY
  fi

  cat > "$INST_DIR/credentials.conf" <<CRED
# credentials for mjjbox auto-checkin
username=${USERNAME}
password=${PASSWORD}
serverchan=${SERVERCHAN_KEY}
# base 可选，默认 https://mjjbox.com
# base=https://mjjbox.com
CRED
  chmod 600 "$INST_DIR/credentials.conf"
  echoinfo "凭据已写入: $INST_DIR/credentials.conf (权限 600)"

  # systemd 单元创建选项
  read -rp "是否创建 systemd 单元与 timer（每天自动签到）？(Y/n): " ENABLE_TIMER
  ENABLE_TIMER=${ENABLE_TIMER:-Y}
  read -rp "是否启用开机时运行一次（开机启动）？(Y/n): " ENABLE_BOOT
  ENABLE_BOOT=${ENABLE_BOOT:-Y}

  if [[ "$ENABLE_TIMER" =~ ^([yY])$ ]]; then
    echoinfo "写入 systemd unit: ${SERVICE_NAME} 与 timer: ${TIMER_NAME}"
    cat > /etc/systemd/system/${SERVICE_NAME} <<SVC
[Unit]
Description=mjjbox auto checkin
After=network-online.target

[Service]
Type=oneshot
User=${OWNER}
WorkingDirectory=${INST_DIR}
ExecStart=${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf
TimeoutStartSec=120
SVC

    cat > /etc/systemd/system/${TIMER_NAME} <<TMR
[Unit]
Description=Run mjjbox checkin daily

[Timer]
# 每天 03:00 执行，若你想修改时间可编辑此文件
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
TMR

    systemctl daemon-reload
    echoinfo "启用并启动 timer: ${TIMER_NAME}"
    systemctl enable --now ${TIMER_NAME}
  else
    echoinfo "跳过创建 systemd timer。你可以手动运行: ${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf"
  fi

  # 创建开机运行一次的 service（enable 使其在开机时运行）
  if [[ "$ENABLE_BOOT" =~ ^([yY])$ ]]; then
    echoinfo "创建开机启动 service: ${BOOT_SERVICE_NAME}"
    cat > /etc/systemd/system/${BOOT_SERVICE_NAME} <<BSVC
[Unit]
Description=mjjbox auto checkin at boot
After=network-online.target

[Service]
Type=oneshot
User=${OWNER}
WorkingDirectory=${INST_DIR}
ExecStart=${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
BSVC

    systemctl daemon-reload
    echoinfo "启用 ${BOOT_SERVICE_NAME}（下次开机将运行）；同时尝试立即运行一次测试..."
    systemctl enable ${BOOT_SERVICE_NAME} || echowarn "启用 ${BOOT_SERVICE_NAME} 失败"
    systemctl start ${BOOT_SERVICE_NAME} || echowarn "立即启动 ${BOOT_SERVICE_NAME} 失败（查看 journal 获取日志）"
  fi

  echoinfo "安装已完成。"
  echoinfo "你可以用下面命令测试（带 debug 输出）:"
  echo "  ${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf --debug"
  echoinfo "查看 timer 状态: systemctl status ${TIMER_NAME}"
  echoinfo "查看开机服务状态: systemctl status ${BOOT_SERVICE_NAME}"
  echoinfo "如需修改定时任务时间，编辑 /etc/systemd/system/${TIMER_NAME} 后执行: systemctl daemon-reload"
}

do_uninstall() {
  read -rp "请输入安装目录 (默认: ${DEFAULT_DIR}): " INST_DIR
  INST_DIR=${INST_DIR:-$DEFAULT_DIR}

  if [ -d "$INST_DIR" ]; then
    read -rp "确认删除 ${INST_DIR} 及其内容? (此操作不可恢复) (y/N): " CONF
    if [[ ! "$CONF" =~ ^([yY])$ ]]; then
      echo "取消卸载"
      exit 0
    fi
  fi

  echoinfo "停止并移除 systemd 单元（如果存在）..."
  if systemctl list-units --full -all | grep -q "${TIMER_NAME}"; then
    systemctl disable --now ${TIMER_NAME} || true
  fi
  if systemctl list-units --full -all | grep -q "${SERVICE_NAME}"; then
    systemctl disable --now ${SERVICE_NAME} || true
  fi
  if systemctl list-units --full -all | grep -q "${BOOT_SERVICE_NAME}"; then
    systemctl disable --now ${BOOT_SERVICE_NAME} || true
  fi
  rm -f /etc/systemd/system/${SERVICE_NAME} /etc/systemd/system/${TIMER_NAME} /etc/systemd/system/${BOOT_SERVICE_NAME} || true
  systemctl daemon-reload || true

  if [ -d "$INST_DIR" ]; then
    echoinfo "删除安装目录 $INST_DIR ..."
    rm -rf "$INST_DIR"
  else
    echoinfo "安装目录不存在: $INST_DIR"
  fi

  echoinfo "卸载完成。"
}

# entry
echowarn "本脚本将以 root 身份直接执行系统级安装/配置。请确保你了解凭据将以明文保存在安装目录下。"
show_menu
