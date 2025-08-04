#!/usr/bin/env python3
"""
autodeploy_ec2.py – non-interactive end-to-end deployment helper
Prereqs on your local machine:
  pip install paramiko fabric boto3
  export AWS_PROFILE=your-profile  # or configure credentials another way
"""

import os
import subprocess
import tarfile
import boto3
from fabric import Connection
from pathlib import Path
from tempfile import TemporaryDirectory

# ---------- 1.  CONFIG SECTION  ----------
REPO_URL          = "https://github.com/Arvo-AI/hello_world.git"
INSTANCE_ID       = "i-08570b74431f51aa0"        # EC2 instance you already started
AWS_REGION        = "us-east-1"
SSH_KEY_LOCAL     = "ec2-user@54.89.13.92"  # key pair you used for the instance
SSH_USER          = "ec2-user"               # or ubuntu, centos, etc.
APP_PORT          = 8080                     # port flask app uses
SERVICE_NAME      = "hello-world"            # name for systemd service
PYTHON_VERSION    = "python3"                # remote python executable
# -----------------------------------------

ec2 = boto3.resource("ec2", region_name=AWS_REGION)
instance = ec2.Instance(INSTANCE_ID)
public_ip = instance.public_ip_address

print(f"[+] Using EC2 {INSTANCE_ID} at {public_ip}")

# ---------- 2.  PACKAGE CODE LOCALLY ----------
with TemporaryDirectory() as tmp:
    # clone/shallow-copy repo
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, tmp],
        check=True
    )
    # create a tarball for fast upload
    tar_path = Path(tmp) / "app.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(tmp, arcname="app")

    # ---------- 3.  COPY & INSTALL REMOTELY ----------
    conn = Connection(
        host=public_ip,
        user=SSH_USER,
        connect_kwargs={"key_filename": SSH_KEY_LOCAL, "look_for_keys": False}
    )

    print("[+] Uploading code …")
    conn.put(str(tar_path), remote="/tmp/app.tar.gz")

    cmds = [
        "sudo yum -y update || true",
        "sudo yum -y install git gcc",
        f"mkdir -p ~/deploy && tar -xzf /tmp/app.tar.gz -C ~/deploy --strip-components 1",
        f"cd ~/deploy && {PYTHON_VERSION} -m venv venv",
        "source ~/deploy/venv/bin/activate && pip install --upgrade pip",
        "source ~/deploy/venv/bin/activate && pip install -r ~/deploy/requirements.txt",
        # patch localhost to public ip so links work
        f"sed -i 's/localhost/{public_ip}/g' $(grep -rl localhost ~/deploy || true)",
        # systemd unit
        f'''printf '%s\n' "[Unit]"
"Description=Hello World Flask service"
"After=network.target"
"[Service]"
"User={SSH_USER}"
"WorkingDirectory=/home/{SSH_USER}/deploy"
"ExecStart=/home/{SSH_USER}/deploy/venv/bin/gunicorn -w 2 -b 0.0.0.0:{APP_PORT} app:app"
"Restart=always"
"[Install]"
"WantedBy=multi-user.target" | sudo tee /etc/systemd/system/{SERVICE_NAME}.service
''',
        f"sudo systemctl daemon-reload",
        f"sudo systemctl enable {SERVICE_NAME}",
        f"sudo systemctl restart {SERVICE_NAME}"
    ]
    print("[+] Running remote provisioning …")
    conn.run(" && ".join(cmds), pty=True)

    # ---------- 4.  OPEN SG PORT ----------
    sg_id = instance.security_groups[0]["GroupId"]
    ec2_client = boto3.client("ec2", region_name=AWS_REGION)
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpProtocol="tcp",
            FromPort=APP_PORT,
            ToPort=APP_PORT,
            CidrIp="0.0.0.0/0"
        )
        print(f"[+] Port {APP_PORT} opened on security group {sg_id}")
    except ec2_client.exceptions.ClientError as e:
        if "InvalidPermission.Duplicate" in str(e):
            print("[i] Port already open, continuing …")
        else:
            raise

print(f"[✓]  App should now be live at:  http://{public_ip}:{APP_PORT}/")
