from textwrap import dedent

def get_vm_bootstrap_script(env_block):
    return dedent(f"""\
#!/usr/bin/env bash
set -e

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y docker.io docker-compose-v2 docker-buildx git
systemctl enable --now docker

rm -rf /home/azureuser/SMACOP
git clone https://github.com/jackpham1019/SMACOP.git /home/azureuser/SMACOP

cd /home/azureuser/SMACOP/app

cat > .env <<EOF
{env_block}
EOF

docker compose up -d --build

docker compose ps
sleep 5
curl -I http://localhost:8081/health || curl -I http://localhost:8081/

# Fix folder permissions
chown -R azureuser:azureuser /home/azureuser/SMACOP
""")