#!/bin/bash

# GCP VM 快速部署腳本
# 在 GCP VM 上運行此腳本來部署 Hummingbot

set -e

# 可用環境變數覆寫：DOCKER_IMAGE、DOCKER_USERNAME、IMAGE_NAME、TAG
DOCKER_USERNAME="${DOCKER_USERNAME:-skywalker0803r}"
IMAGE_NAME="${IMAGE_NAME:-hummingbot-adaptive}"
TAG="${TAG:-latest}"
DOCKER_IMAGE="${DOCKER_IMAGE:-${DOCKER_USERNAME}/${IMAGE_NAME}:${TAG}}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-}"
HOST_ARCH="$(uname -m)"

pull_image() {
  local image="$1"
  local platform="$2"

  if [ -n "$platform" ]; then
    docker pull --platform "$platform" "$image"
  else
    docker pull "$image"
  fi
}

echo "=== GCP VM Hummingbot 快速部署 ==="
echo "目標鏡像: ${DOCKER_IMAGE}"
if [ -n "${DOCKER_PLATFORM}" ]; then
  echo "指定平台: ${DOCKER_PLATFORM}"
elif [ "${HOST_ARCH}" = "arm64" ] || [ "${HOST_ARCH}" = "aarch64" ]; then
  echo "偵測到 ARM64 主機，若鏡像無 ARM64 manifest 將自動改用 linux/amd64"
fi
echo ""

# 檢查 Docker 是否安裝
if ! command -v docker &> /dev/null; then
    echo "🔧 安裝 Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "⚠️  請登出並重新登入以使用 Docker，然後重新運行此腳本"
    exit 1
fi

# 檢查 Docker Compose 是否安裝
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "🔧 安裝 Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# 創建工作目錄
WORK_DIR="$HOME/hummingbot"
if [ ! -d "$WORK_DIR" ]; then
    echo "📁 創建工作目錄..."
    mkdir -p $WORK_DIR
fi

cd $WORK_DIR

# 創建必要的目錄
echo "📁 創建必要的目錄..."
mkdir -p conf conf/connectors conf/strategies conf/controllers conf/scripts logs data certs scripts controllers

# 拉取最新鏡像
echo "📥 拉取最新鏡像..."
PULL_OUTPUT=""
if ! PULL_OUTPUT=$(pull_image "${DOCKER_IMAGE}" "${DOCKER_PLATFORM}" 2>&1); then
    if [ -z "${DOCKER_PLATFORM}" ] && ([ "${HOST_ARCH}" = "arm64" ] || [ "${HOST_ARCH}" = "aarch64" ]) && echo "${PULL_OUTPUT}" | grep -qi "no matching manifest"; then
        echo "${PULL_OUTPUT}"
        echo "ℹ️  偵測到 ARM64 主機且鏡像缺少 ARM64 manifest，改用 linux/amd64 重試..."
        DOCKER_PLATFORM="linux/amd64"
        if ! PULL_OUTPUT=$(pull_image "${DOCKER_IMAGE}" "${DOCKER_PLATFORM}" 2>&1); then
            echo "${PULL_OUTPUT}"
            echo "❌ 拉取鏡像失敗: ${DOCKER_IMAGE}"
            echo ""
            echo "可能原因:"
            echo "1) 鏡像不存在（名稱或標籤錯誤）"
            echo "2) 倉庫是私有的，尚未登入 Docker Hub"
            echo "3) Docker Hub 用戶名設定錯誤"
            echo "4) 主機架構與鏡像平台不相容"
            echo ""
            echo "建議檢查:"
            echo "- 先執行: docker login"
            echo "- 確認鏡像: docker pull skywalker0803r/hummingbot-adaptive:latest"
            echo "- 指定平台: DOCKER_PLATFORM=linux/amd64 ./gcp_deploy.sh"
            echo "- 或覆寫鏡像: DOCKER_IMAGE=<owner>/<repo>:<tag> ./gcp_deploy.sh"
            exit 1
        fi
    else
        echo "${PULL_OUTPUT}"
        echo "❌ 拉取鏡像失敗: ${DOCKER_IMAGE}"
        echo ""
        echo "可能原因:"
        echo "1) 鏡像不存在（名稱或標籤錯誤）"
        echo "2) 倉庫是私有的，尚未登入 Docker Hub"
        echo "3) Docker Hub 用戶名設定錯誤"
        echo "4) 主機架構與鏡像平台不相容"
        echo ""
        echo "建議檢查:"
        echo "- 先執行: docker login"
        echo "- 確認鏡像: docker pull skywalker0803r/hummingbot-adaptive:latest"
        echo "- 指定平台: DOCKER_PLATFORM=linux/amd64 ./gcp_deploy.sh"
        echo "- 或覆寫鏡像: DOCKER_IMAGE=<owner>/<repo>:<tag> ./gcp_deploy.sh"
        exit 1
    fi
fi

if [ -n "${PULL_OUTPUT}" ]; then
    echo "${PULL_OUTPUT}"
fi

PLATFORM_YAML=""
if [ -n "${DOCKER_PLATFORM}" ]; then
    PLATFORM_YAML="    platform: ${DOCKER_PLATFORM}"
fi

# 下載 docker-compose.prod.yml
echo "📥 下載配置文件..."
cat > docker-compose.prod.yml << EOF
services:
  hummingbot:
    container_name: hummingbot
    image: ${DOCKER_IMAGE}
${PLATFORM_YAML}
    volumes:
      - ./conf:/home/hummingbot/conf
      - ./conf/connectors:/home/hummingbot/conf/connectors
      - ./conf/strategies:/home/hummingbot/conf/strategies
      - ./conf/controllers:/home/hummingbot/conf/controllers
      - ./conf/scripts:/home/hummingbot/conf/scripts
      - ./logs:/home/hummingbot/logs
      - ./data:/home/hummingbot/data
      - ./certs:/home/hummingbot/certs
      - ./scripts:/home/hummingbot/scripts
      - ./controllers:/home/hummingbot/controllers
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
    tty: true
    stdin_open: true
    network_mode: host
    restart: unless-stopped
EOF

# 停止舊容器（如果存在）
if [ "$(docker ps -aq -f name=hummingbot)" ]; then
    echo "🛑 停止舊容器..."
    docker compose -f docker-compose.prod.yml down
fi

# 啟動 Hummingbot
echo "🚀 啟動 Hummingbot..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "✅ 部署完成！"
echo ""
echo "📋 管理命令:"
echo "  查看狀態: docker ps"
echo "  查看日誌: docker logs -f hummingbot"
echo "  進入容器: docker attach hummingbot"
echo "  停止服務: docker compose -f docker-compose.prod.yml down"
echo "  重啟服務: docker compose -f docker-compose.prod.yml restart"
echo ""
echo "📁 工作目錄: $WORK_DIR"
echo "⚙️  配置目錄: $WORK_DIR/conf"
echo "📜 日誌目錄: $WORK_DIR/logs"