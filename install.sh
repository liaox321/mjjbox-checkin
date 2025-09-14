#!/usr/bin/env bash
set -euo pipefail

# install.sh - mjjbox 自动签到 安装 / 卸载 脚本（自动安装依赖 + 开机启动支持）
# 使用方法：
#   chmod +x install.sh
#   ./install.sh
#
# 功能：
# - 交互式安装：创建安装目录、创建虚拟环境、安装 python 依赖
# - 可选：自动安装系统依赖（按发行版选择包管理器）
# - 可选：创建 systemd timer（每天运行）并创建一个在开机时运行一次的 service（开机启动）
# - 支持填写明文 credentials（username/password/serverchan）
# - 卸载：移除目录、systemd 单元

DEFAULT_DIR="/opt/mjjbox_checkin"
PYTHON_CMD="/usr/bin/python3"
SERVICE_NAME="mjjbox-checkin.service"
TIMER_NAME="mjjbox-checkin.timer"
BOOT_SERVICE_NAME="mjjbox-checkin-at-boot.service"

# ---------- helper ----------
echoinfo(){ echo -e "\033[1;34m[INFO]\033[0m $*"; }
echowarn(){ echo -e "\033[1;33m[WARN]\033[0m $*"; }
echoerr(){ echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

require_sudo() {
  if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echoerr "当前非 root 且系统没有 sudo，请切换到 root 或先安装 sudo。"
      exit 1
    fi
  fi
}

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
    echowarn "未检测到支持的包管理器（apt/dnf/yum/pacman/apk/zypper）。请手动安装以下依赖：python3 python3-venv python3-pip curl"
    return 1
  fi

  echoinfo "检测到包管理器: $PKG，开始安装系统依赖（python3/venv/pip/curl）..."
  case "$PKG" in
    apt)
      SUDO_CMD="sudo"
      $SUDO_CMD apt-get update -y
      $SUDO_CMD apt-get install -y python3 python3-venv python3-pip curl || return 1
      ;;
    dnf)
      SUDO_CMD="sudo"
      $SUDO_CMD dnf install -y python3 python3-venv python3-pip curl || return 1
      ;;
    yum)
      SUDO_CMD="sudo"
      $SUDO_CMD yum install -y python3 python3-venv python3-pip curl || return 1
      ;;
    pacman)
      SUDO_CMD="sudo"
      $SUDO_CMD pacman -Sy --noconfirm python python-virtualenv python-pip curl || return 1
      ;;
    apk)
      SUDO_CMD="sudo"
      $SUDO_CMD apk add --no-cache python3 py3-virtualenv py3-pip curl || return 1
      ;;
    zypper)
      SUDO_CMD="sudo"
      $SUDO_CMD zypper install -y python3 python3-virtualenv python3-pip curl || return 1
      ;;
    *)
      echowarn "未知包管理器: $PKG，请手动安装依赖"
      return 1
      ;;
  esac
  echoinfo "系统依赖安装完成（或已存在）"
  return 0
}

# ---------- main actions ----------
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

do_install() {
  read -rp "安装目录 (默认: ${DEFAULT_DIR}): " INST_DIR
  INST_DIR=${INST_DIR:-$DEFAULT_DIR}

  read -rp "是否自动尝试安装系统依赖（python3/venv/pip/curl）？(Y/n): " AUTO_SYS
  AUTO_SYS=${AUTO_SYS:-Y}

  if [[ "$AUTO_SYS" =~ ^([yY])$ ]]; then
    require_sudo
    if ! install_system_deps; then
      echowarn "自动安装系统依赖失败或不完整，请根据提示手动安装后再运行本脚本。"
      read -rp "是否继续（跳过系统依赖安装）？(y/N): " cont
      if [[ ! "$cont" =~ ^([yY])$ ]]; then
        echoinfo "取消安装。"
        exit 1
      fi
    fi
  fi

  # 尝试寻找可用的 python 可执行文件
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
  sudo mkdir -p "$INST_DIR"
  sudo chown "$(whoami):$(whoami)" "$INST_DIR"

  echoinfo "创建 Python 虚拟环境..."
  "$PYBIN" -m venv "$INST_DIR/venv"
  # shellcheck disable=SC1090
  source "$INST_DIR/venv/bin/activate"

  echoinfo "升级 pip 并安装 python 库 (requests, beautifulsoup4)..."
  pip install --upgrade pip >/dev/null
  pip install requests beautifulsoup4 >/dev/null

  echoinfo "写入 checkin.py 到 $INST_DIR/checkin.py"
  # 这里写入最新的 checkin.py 代码（请根据需要替换或直接覆盖）
  cat > "$INST_DIR/checkin.py" <<'PYEOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 请把你之前确认的完整 checkin.py 内容放在这里，或在安装完成后用我提供的最新 checkin.py 覆盖此文件。
# 为避免脚本过长，此处是占位文本；install 完成后请确保 checkin.py 为最新版本并 chmod +x。
# 如果你希望本脚本自动写入完整 checkin.py，请告诉我我可以把完整代码嵌入此文件。
PYEOF

  chmod +x "$INST_DIR/checkin.py"

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
    sudo bash -c "cat > /etc/systemd/system/${SERVICE_NAME}" <<SVC
[Unit]
Description=mjjbox auto checkin
After=network-online.target

[Service]
Type=oneshot
User=$(whoami)
WorkingDirectory=${INST_DIR}
ExecStart=${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf
TimeoutStartSec=120
SVC

    sudo bash -c "cat > /etc/systemd/system/${TIMER_NAME}" <<TMR
[Unit]
Description=Run mjjbox checkin daily

[Timer]
# 每天 03:00 执行，若你想修改时间可编辑此文件
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
TMR

    sudo systemctl daemon-reload
    echoinfo "启用并启动 timer: ${TIMER_NAME}"
    sudo systemctl enable --now ${TIMER_NAME}
  else
    echoinfo "跳过创建 systemd timer。你可以手动运行: ${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf"
  fi

  # 创建开机运行一次的 service（enable 使其在开机时运行）
  if [[ "$ENABLE_BOOT" =~ ^([yY])$ ]]; then
    echoinfo "创建开机启动 service: ${BOOT_SERVICE_NAME}"
    sudo bash -c "cat > /etc/systemd/system/${BOOT_SERVICE_NAME}" <<BSVC
[Unit]
Description=mjjbox auto checkin at boot
After=network-online.target

[Service]
Type=oneshot
User=$(whoami)
WorkingDirectory=${INST_DIR}
ExecStart=${INST_DIR}/venv/bin/python ${INST_DIR}/checkin.py --cred ${INST_DIR}/credentials.conf
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
BSVC

    sudo systemctl daemon-reload
    # 启用开机启动（会在下次开机自动运行）。同时这里也尝试立即启动一次（可选）
    echoinfo "启用 ${BOOT_SERVICE_NAME}（下次开机将运行）；同时立即运行一次测试..."
    sudo systemctl enable ${BOOT_SERVICE_NAME} || true
    sudo systemctl start ${BOOT_SERVICE_NAME} || echowarn "启动 ${BOOT_SERVICE_NAME} 失败（查看 systemctl status 获取日志）"
  fi

  echoinfo "安装已完成。请检查并替换 $INST_DIR/checkin.py 为最新脚本（如果此处为占位），并确保权限为可执行。"
  echoinfo "运行测试（debug 模式）:"
  echo "  $INST_DIR/venv/bin/python $INST_DIR/checkin.py --cred $INST_DIR/credentials.conf --debug"
  echoinfo "查看 timer 状态: sudo systemctl status ${TIMER_NAME}"
  echoinfo "查看开机服务状态: sudo systemctl status ${BOOT_SERVICE_NAME}"
  echoinfo "如果想修改定时任务时间，编辑 /etc/systemd/system/${TIMER_NAME} 后执行: sudo systemctl daemon-reload"
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
    sudo systemctl disable --now ${TIMER_NAME} || true
  fi
  if systemctl list-units --full -all | grep -q "${SERVICE_NAME}"; then
    sudo systemctl disable --now ${SERVICE_NAME} || true
  fi
  if systemctl list-units --full -all | grep -q "${BOOT_SERVICE_NAME}"; then
    sudo systemctl disable --now ${BOOT_SERVICE_NAME} || true
  fi
  sudo rm -f /etc/systemd/system/${SERVICE_NAME} /etc/systemd/system/${TIMER_NAME} /etc/systemd/system/${BOOT_SERVICE_NAME} || true
  sudo systemctl daemon-reload || true

  if [ -d "$INST_DIR" ]; then
    echoinfo "删除安装目录 $INST_DIR ..."
    sudo rm -rf "$INST_DIR"
  else
    echoinfo "安装目录不存在: $INST_DIR"
  fi

  echoinfo "卸载完成。"
}

# entry
if [ "$(id -u)" -eq 0 ]; then
  echowarn "注意：建议以普通用户运行此脚本（会在需要时使用 sudo 提权）。"
fi

show_menu
