"""远程部署/更新工具（paramiko SSH自动化）。

用法：
  .venv/Scripts/python deploy/remote.py deploy --host 1.2.3.4 --user root [--password xxx | --key file]
      全新部署：上传代码包 → 写 .env → docker compose up → nginx 配置 → 自测
  .venv/Scripts/python deploy/remote.py update  --host ... [同上]
      更新：只上传代码并重建容器（.env 保留）
  .venv/Scripts/python deploy/remote.py status --host ...
      查看：容器状态 + curl 自测结果

参数：
  --host       服务器IP或域名（必填）
  --user       SSH用户（默认 root）
  --password   SSH密码（与 --key 二选一；也可用环境变量 WEILU_SSH_PW）
  --key        SSH私钥路径
  --port       SSH端口（默认22）
  --domain     公网域名（nginx server_name，如 weilu.example.com；缺省用路径前缀模式）
  --base-path  服务器上的安装目录（默认 /opt/weilu-agent）
  --api-key    本服务的 WEILU_API_KEY（缺省自动生成并打印）
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import paramiko

PROJECT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules", "tests", "scripts", "deploy", ".idea"}
EXCLUDE_FILES = {".env", "server_run.log", "postcard_preview.png", "painting_test.png"}

NGINX_PATH_PREFIX = """
# >>> weilu-agent（路径前缀模式）加到现有 443 server 块内
location /weilu/ {
    proxy_pass http://127.0.0.1:8200/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
# <<< weilu-agent
"""

NGINX_SUBDOMAIN = """server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate     {cert};
    ssl_certificate_key {cert_key};
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
    location / {{
        proxy_pass http://127.0.0.1:8200;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
    }}
}}
server {{
    listen 80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}
"""


class Remote:
    def __init__(self, host: str, user: str, password: str | None, key: str | None, port: int = 22):
        self.cli = paramiko.SSHClient()
        self.cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.cli.connect(host, port=port, username=user, password=password,
                         key_filename=key, timeout=15)
        self.host = host

    def run(self, cmd: str, timeout: int = 300, check: bool = True) -> tuple[int, str]:
        _, out, err = self.cli.exec_command(cmd, timeout=timeout)
        code = out.channel.recv_exit_status()
        text = (out.read() + err.read()).decode("utf-8", "replace").strip()
        print(f"$ {cmd[:100]}{'…' if len(cmd) > 100 else ''}")
        if text:
            print(text[-1500:])
        if check and code != 0:
            raise RuntimeError(f"command failed ({code}): {cmd[:120]}")
        return code, text

    def upload_dir(self, base_path: str) -> None:
        """SFTP 上传项目（排除开发文件）。"""
        sftp = self.cli.open_sftp()
        try:
            self.run(f"mkdir -p {base_path}")
            count = 0
            for p in PROJECT.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(PROJECT).as_posix()
                if any(part in EXCLUDE_DIRS for part in Path(rel).parts[:-1]) or Path(rel).name in EXCLUDE_FILES:
                    continue
                if rel == "postcard_preview.png":
                    continue
                target = f"{base_path}/{rel}"
                self.run(f"mkdir -p $(dirname {target})")
                sftp.put(str(p), target)
                count += 1
            print(f"uploaded {count} files -> {base_path}")
        finally:
            sftp.close()


def make_env(api_key: str, public_base_url: str) -> str:
    """从本地 .env 读取供应商配置，生成生产 .env（密钥不上屏）。"""
    local = {}
    env_file = PROJECT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                local[k.strip()] = v.strip()
    lines = [
        f"WEILU_API_KEY={api_key}",
        f"PUBLIC_BASE_URL={public_base_url}",
        "AI_PROVIDER_IDS=XCODE_MAIN,SILICONFLOW_MAIN",
    ]
    for ident in ("XCODE_MAIN", "SILICONFLOW_MAIN"):
        for suffix in ("BASE_URL", "API_KEY", "MODEL"):
            k = f"AI_PROVIDER_{ident}_{suffix}"
            v = local.get(k, "")
            if v:
                lines.append(f"{k}={v}")
    lines.append("IMAGE_MODEL=gpt-image-2")
    return "\n".join(lines) + "\n"


def cmd_deploy(args, remote: Remote, base: str) -> None:
    print("== 1/5 上传代码 ==")
    remote.upload_dir(base)
    api_key = args.api_key or ("sk-weilu-" + secrets.token_hex(16))
    if args.domain:
        public_base = f"https://{args.domain}"
    else:
        public_base = f"http://{remote.host}:8200"  # 路径前缀模式由用户自己补域名
    print(f"== 2/5 写入 .env（API Key: {api_key}）==")
    sftp = remote.cli.open_sftp()
    with sftp.open(f"{base}/.env", "w") as f:
        f.write(make_env(api_key, public_base))
    sftp.close()
    print("== 3/5 构建并启动容器 ==")
    remote.run(f"cd {base} && (docker compose up -d --build 2>&1 || docker-compose up -d --build 2>&1)", timeout=600)
    time.sleep(6)
    print("== 4/5 本机自测 ==")
    remote.run(f"sleep 3; curl -s -m 10 http://127.0.0.1:8200/healthz")
    code, _ = remote.run(
        f"curl -s -m 30 -o /dev/null -w '%{{http_code}}' -X POST http://127.0.0.1:8200/v1/chat/completions "
        f"-H 'Authorization: Bearer {api_key}' -H 'Content-Type: application/json' "
        f"-d '{{\"stream\":true,\"max_tokens\":1,\"messages\":[{{\"role\":\"user\",\"content\":\"hi\"}}]}}'",
        check=False,
    )
    print("== 5/5 Nginx ==")
    if args.domain:
        remote.run(f"test -f /etc/letsencrypt/live/{args.domain}/fullchain.pem && echo cert-exists || echo cert-missing（需要先 certbot 签发）", check=False)
        print(NGINX_SUBDOMAIN.format(domain=args.domain,
                                     cert=f"/etc/letsencrypt/live/{args.domain}/fullchain.pem",
                                     cert_key=f"/etc/letsencrypt/live/{args.domain}/privkey.pem"))
        print("↑ 把上面配置写入 /etc/nginx/conf.d/weilu.conf（certbot 签发后）")
    else:
        print("未指定 --domain，采用路径前缀模式。把下面片段加入现有 server 块：")
        print(NGINX_PATH_PREFIX)
    print(f"\n✅ 部署完成。API Key（保存好，接入清小搭用）：{api_key}")


def cmd_update(args, remote: Remote, base: str) -> None:
    print("== 更新：上传代码 → 重建容器（.env 保留）==")
    remote.run(f"test -f {base}/.env", check=True)  # 确保是已部署过的目录
    remote.upload_dir(base)
    remote.run(f"cd {base} && (docker compose up -d --build 2>&1 || docker-compose up -d --build 2>&1)", timeout=600)
    time.sleep(5)
    remote.run("curl -s -m 10 http://127.0.0.1:8200/healthz")
    print("✅ 更新完成")


def cmd_status(args, remote: Remote, base: str) -> None:
    remote.run(f"docker ps --filter name=weilu --format '{{{{.Names}}}} {{{{.Status}}}}'", check=False)
    remote.run("curl -s -m 10 http://127.0.0.1:8200/healthz", check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["deploy", "update", "status"])
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default=None)
    ap.add_argument("--key", default=None)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--domain", default=None)
    ap.add_argument("--base-path", default="/opt/weilu-agent")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    password = args.password or __import__("os").environ.get("WEILU_SSH_PW")
    if not password and not args.key:
        print("需要 --password 或 --key（或环境变量 WEILU_SSH_PW）")
        sys.exit(1)

    remote = Remote(args.host, args.user, password, args.key, args.port)
    try:
        if args.action == "deploy":
            cmd_deploy(args, remote, args.base_path)
        elif args.action == "update":
            cmd_update(args, remote, args.base_path)
        else:
            cmd_status(args, remote, args.base_path)
    finally:
        remote.cli.close()


if __name__ == "__main__":
    main()
