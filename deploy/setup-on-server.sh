#!/usr/bin/env bash
# 清心圆桌 · 服务器一键部署脚本（在有 Docker 的服务器上运行）
#
# 用法：
#   bash setup-on-server.sh <git仓库URL> <子域名> [安装目录，默认/opt/weilu-agent]
# 例：
#   bash setup-on-server.sh git@github.com:zhuangly20/weilu-agent.git weilu.example.com
#   bash setup-on-server.sh https://github.com/zhuangly20/weilu-agent.git weilu.example.com
#
# 前置条件：
#   1. DNS已把子域名指向本服务器
#   2. 服务器已装 Docker 和 Docker Compose（史记项目同款环境）
#   3. 已装 nginx 和 certbot（用于HTTPS；没有certbot见脚本末尾提示）
#   4. .env 已就位（首次运行若不存在会生成模板并提示填写模型key后重跑）
set -euo pipefail

REPO_URL=${1:?用法: setup-on-server.sh <仓库URL> <子域名> [安装目录]}
DOMAIN=${2:?需要子域名，如 weilu.example.com}
BASE=${3:-/opt/weilu-agent}

echo ">>> 1/6 检查环境"
command -v docker >/dev/null || { echo "✗ 未安装 Docker"; exit 1; }
docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null 2>&1 || { echo "✗ 未安装 Docker Compose"; exit 1; }

echo ">>> 2/6 获取代码"
if [ -d "$BASE/.git" ]; then
  git -C "$BASE" pull --ff-only
else
  git clone "$REPO_URL" "$BASE"
fi

echo ">>> 3/6 检查 .env（含模型密钥，不入仓库）"
if [ ! -f "$BASE/.env" ]; then
  API_KEY="sk-weilu-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cat > "$BASE/.env" <<EOF
WEILU_API_KEY=$API_KEY
PUBLIC_BASE_URL=https://$DOMAIN
AI_PROVIDER_IDS=XCODE_MAIN
AI_PROVIDER_XCODE_MAIN_BASE_URL=https://xcode.best/v1
AI_PROVIDER_XCODE_MAIN_API_KEY=在这里填xcode网关的key
AI_PROVIDER_XCODE_MAIN_MODEL=gpt-5.4
IMAGE_MODEL=gpt-image-2
EOF
  echo "!! 已生成 .env 模板：请编辑 $BASE/.env 填入模型 API_KEY（参考本地 weilu-agent/.env），然后重新运行本脚本"
  exit 1
fi
API_KEY=$(grep '^WEILU_API_KEY=' "$BASE/.env" | cut -d= -f2)

echo ">>> 4/6 构建并启动容器"
cd "$BASE"
docker compose up -d --build 2>/dev/null || docker-compose up -d --build
sleep 6
curl -sf -m 10 http://172.17.0.1:8200/healthz >/dev/null || { echo "✗ 健康检查失败，容器日志："; docker logs weilu-agent --tail 40; exit 1; }
echo "✓ 容器运行正常"

echo ">>> 5/6 Nginx + HTTPS 证书"
if [ -d /etc/nginx ] && command -v nginx >/dev/null; then
  cat > /etc/nginx/conf.d/weilu.conf <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
EOF
  nginx -t && (systemctl reload nginx || nginx -s reload)
  echo "✓ Nginx 已配置（80端口）"
  if command -v certbot >/dev/null; then
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email -m admin@"$DOMAIN" 2>/dev/null \
       || certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email; then
      echo "✓ HTTPS 证书已签发并自动配置443"
    else
      echo "!! certbot 签发失败（检查DNS是否已生效：ping $DOMAIN）。可稍后手动：certbot --nginx -d $DOMAIN"
    fi
  else
    echo "!! 未安装 certbot：请手动为 $DOMAIN 配置证书（参考仓库 deploy/nginx-weilu.conf.example）"
  fi
else
  echo "!! 未检测到 nginx：请自行把 $BASE/deploy/nginx-weilu.conf.example 接入你的反代（注意SSE需关缓冲）"
fi

echo ">>> 6/6 公网自测"
sleep 2
curl -s -m 15 -o /dev/null -w "公网 /v1/models 状态码: %{http_code}\n" \
  -H "Authorization: Bearer $API_KEY" "https://$DOMAIN/v1/models" || \
  curl -s -m 15 -o /dev/null -w "公网 /v1/models 状态码(https失败试http): %{http_code}\n" \
  -H "Authorization: Bearer $API_KEY" "http://$DOMAIN/v1/models" || true

echo
echo "=============================================="
echo "✅ 部署完成"
echo "   baseUrl:  https://$DOMAIN/v1"
echo "   API Key:  $API_KEY"
echo "   （拿这两项去清小搭「标准协议接入」向导填写）"
echo "=============================================="
