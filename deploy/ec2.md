# AWS EC2 deployment (authenticated, HTTPS)

One EC2 instance runs the whole stack: host nginx terminates TLS and proxies
to the Next.js frontend, which reaches FastAPI over a private Docker network.
Secrets come from AWS Secrets Manager through the instance's IAM role, so
there are no AWS keys anywhere. Images are built by CI and pulled, never
built on the box. Every file referenced here is in [`deploy/aws/`](aws/).

```
Internet ──443──▶ nginx (TLS, HSTS, rate limits, 21 MB body cap)
         ──80───▶ 301 → https
                    │ 127.0.0.1:3000
                    ▼
            frontend container  (session check, /api allowlist proxy)
                    │ http://backend:8000  (compose network only, no host port)
                    ▼
            backend container   (JWT verify via JWKS, owner scoping, Gmail)
                    │
     Supabase (Auth, Postgres, Storage) · Anthropic · Langfuse · Gmail IMAP
```

Placeholders used below: `verity.example.com` (your domain), `us-east-1`
(your region), `ACCOUNT_ID`, `your-verity-backups` (an S3 bucket name you
choose). Nothing here has been run against a real AWS account by the
author of these files, so verify each step's output as you go.

## 0. What you need first

- **A domain or subdomain** whose DNS you control. Let's Encrypt needs a
  name, not a bare IP. A free DuckDNS subdomain works.
- **Public GHCR images.** CI pushes `ghcr.io/<owner>/invoice-agent` and
  `ghcr.io/<owner>/verity-web` on every push to `main`. In GitHub, open
  Packages, then each package, then Package settings, and change visibility
  to Public. The images contain no secrets (both `.dockerignore`s exclude
  `.env*`). Note the commit SHA you want to deploy: it's the image tag.
- **The Supabase owner UID.** Sign in once locally (see `docs/auth.md`); the
  login page shows your UID if it isn't allowlisted yet.

## 1. Supabase (dashboard)

1. **Authentication → URL Configuration:**
   - Set **Site URL** to `https://verity.example.com`.
   - Add `https://verity.example.com/auth/callback` to **Redirect URLs**.
2. **Authentication → Sign In / Providers:** turn **Allow new users to sign
   up** off, once your own account exists. The app's allowlist
   (`OWNER_USER_ID`) already blocks everyone else, so this is a second
   layer.
3. **Google Cloud Console → your OAuth client:** the authorized redirect URI
   stays `https://<project-ref>.supabase.co/auth/v1/callback`. It doesn't
   change per deployment.

## 2. Secrets Manager

Create one secret holding the backend's real secrets as JSON:

```bash
aws secretsmanager create-secret --region us-east-1 --name verity/prod \
  --secret-string file://secret.json   # then delete secret.json
```

`secret.json` holds only the actual secrets:

```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "SUPABASE_KEY": "<service-role / secret key>",
  "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
  "LANGFUSE_SECRET_KEY": "sk-lf-...",
  "GMAIL_ADDRESS": "you@gmail.com",
  "GMAIL_APP_PASSWORD": "xxxx xxxx xxxx xxxx"
}
```

Run that from your own machine with your own credentials, not from the
instance. The console works too: Store a new secret, choose Other type of
secret, then Plaintext.

## 3. S3 backup bucket

- Create `your-verity-backups` in the same region. Keep **Block all public
  access** on and default encryption (SSE-S3) on.
- Add a lifecycle rule: prefix `checkpoints/`, expire current versions
  after **30 days**.

## 4. IAM role for the instance

1. Go to IAM → Roles → Create role → AWS service → EC2.
2. Attach the managed policy **AmazonSSMManagedInstanceCore**, so you get a
   shell through Session Manager instead of SSH.
3. Add an inline policy from [`aws/iam-policy.json`](aws/iam-policy.json),
   replacing `REGION`, `ACCOUNT_ID` and `BACKUP_BUCKET`. The role can read
   only that one secret and write only the backup prefix.
4. Name the role `verity-ec2`.

## 5. Security group

| Direction | Port | Source | Why |
|---|---|---|---|
| Inbound | 443/tcp | 0.0.0.0/0, ::/0 | the app |
| Inbound | 80/tcp | 0.0.0.0/0, ::/0 | HTTP→HTTPS redirect, and certbot renewal |
| Inbound | 22/tcp | **none** (preferred), or your IP /32 only | use SSM Session Manager instead |
| Outbound | all | 0.0.0.0/0 | Supabase, Anthropic, Langfuse, Gmail, GHCR, AWS APIs |

Ports 3000 and 8000 are **not** opened. Docker publishes 3000 on
127.0.0.1 only, and 8000 isn't published at all.

## 6. Launch the instance

- **AMI:** Ubuntu Server 24.04 LTS.
- **Instance type:** `t3.small` (2 GB) recommended. A `t3.micro` (1 GB,
  free tier) also works, but add the swap step in section 8.
- **Storage:** 20 GB gp3, **encrypted**.
- **Advanced details:**
  - IAM instance profile: `verity-ec2`.
  - Metadata version: **IMDSv2 only**.
  - Metadata hop limit: 1 is fine. Only the host calls IMDS, never a
    container.
- **Security group:** the one from step 5.
- **Elastic IP:** allocate one and associate it with the instance.

## 7. DNS

Point an **A record** for `verity.example.com` at the Elastic IP, then wait
until `dig +short verity.example.com` returns it.

## 8. Prepare the host

Connect: EC2 → the instance → Connect → **Session Manager**.

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y docker.io docker-compose-v2 nginx certbot jq unattended-upgrades
sudo snap install aws-cli --classic
sudo systemctl enable --now docker
aws sts get-caller-identity   # should show the verity-ec2 role, no keys configured
```

t3.micro only: add 2 GB of swap.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 9. Install the app files

```bash
sudo git clone --depth 1 https://github.com/HumdaanSyed/Multi-Agent-Invoice-Processor.git /opt/verity-src
sudo install -d -m 755 /opt/verity /etc/verity
sudo install -m 644 /opt/verity-src/deploy/aws/docker-compose.yml /opt/verity/
sudo install -m 750 /opt/verity-src/deploy/aws/fetch-secrets.sh /opt/verity-src/deploy/aws/backup.sh /opt/verity/
sudo install -m 644 /opt/verity-src/deploy/aws/verity.service /etc/systemd/system/
sudo install -m 600 /opt/verity-src/deploy/aws/config.env.example /etc/verity/config.env
sudo nano /etc/verity/config.env    # fill in every value; IMAGE_TAG = the commit SHA
```

Test the secrets fetch. It prints a count, never values:

```bash
sudo /opt/verity/fetch-secrets.sh
sudo ls -l /run/verity/secrets.env   # -rw------- root root
```

## 10. TLS certificate and nginx

Get the certificate first, with nginx stopped so certbot can use port 80.
The hooks are saved, so automatic renewal (the `certbot.timer` the package
installs) repeats this stop/start by itself.

```bash
sudo systemctl stop nginx
sudo certbot certonly --standalone -d verity.example.com \
  --agree-tos -m you@example.com --no-eff-email \
  --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx"
sudo rm -f /etc/nginx/sites-enabled/default
sudo install -m 644 /opt/verity-src/deploy/aws/nginx-verity.conf /etc/nginx/conf.d/verity.conf
sudo sed -i 's/verity.example.com/YOUR.DOMAIN/g' /etc/nginx/conf.d/verity.conf
sudo nginx -t && sudo systemctl start nginx
sudo certbot renew --dry-run
```

## 11. Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now verity
sudo docker compose -f /opt/verity/docker-compose.yml --env-file /etc/verity/config.env ps
```

Both containers should reach `healthy`. That can take about 30 seconds.

## 12. Verify

From your laptop:

```bash
curl -sI http://verity.example.com | head -1            # 301
curl -sI https://verity.example.com/healthz | head -1   # 200
curl -s  https://verity.example.com/api/invoices        # {"error":"not_authenticated",...}
curl -s  https://verity.example.com/api/health/ready    # 401: deep readiness isn't reachable anonymously
curl -m 5 http://verity.example.com:8000/health         # must time out / be refused
curl -m 5 http://verity.example.com:3000/               # must time out / be refused
curl -sI https://verity.example.com | grep -i strict    # HSTS header present
```

In a browser, sign in with Google. You should land on the home page with
your email in the header. Upload an invoice from `data/eval/` and watch it
stream live. A second Google account should see "doesn't have access".

## 13. Backups

The only state unique to this box is the LangGraph checkpoint SQLite file:
runs, review state and run ownership. Invoices and the ledger live in
Supabase, and PDFs in Supabase Storage.

```bash
sudo /opt/verity/backup.sh     # run once by hand first
echo 'PATH=/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
17 3 * * * root /opt/verity/backup.sh >> /var/log/verity-backup.log 2>&1' | sudo tee /etc/cron.d/verity-backup
```

Retention is 7 local copies in `/var/backups/verity`, plus 30 days in S3
through the lifecycle rule. For whole-disk recovery, also add an EBS
snapshot policy in **EC2 → Lifecycle Manager**, e.g. daily with 7 kept.

## Operations

**Logs.** Container stdout is json-file, rotated at 10 MB × 5. Auth
failures log only the exception class, and invoice contents aren't logged.

```bash
sudo journalctl -u verity                    # start/stop, secrets fetch
sudo docker compose -f /opt/verity/docker-compose.yml --env-file /etc/verity/config.env logs -f --tail 200
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

**Deploy a new version.** Set `IMAGE_TAG` in `/etc/verity/config.env` to
the new commit SHA, then:

```bash
sudo docker compose -f /opt/verity/docker-compose.yml --env-file /etc/verity/config.env pull
sudo systemctl restart verity
```

**Roll back.** Set `IMAGE_TAG` back to the previous SHA and run the same
two commands. The checkpoint schema is LangGraph's own, so rolling back
across a LangGraph major version may need a restore as well.

**Rotate a secret.** Update the value in Secrets Manager, then run
`sudo systemctl restart verity`. The unit re-fetches on every start.
- **Gmail App Password:** revoke the old one at
  myaccount.google.com/apppasswords.
- **Supabase secret key:** roll it in Project Settings → API Keys.

**Restore the checkpoint DB** (new instance or corruption):

```bash
aws s3 cp s3://your-verity-backups/checkpoints/graph-<stamp>.sqlite.gz /tmp/ && gunzip /tmp/graph-<stamp>.sqlite.gz
C="sudo docker compose -f /opt/verity/docker-compose.yml --env-file /etc/verity/config.env"
$C stop backend frontend
$C run --rm --no-deps -T backend sh -c 'rm -f checkpoints/graph.sqlite-wal checkpoints/graph.sqlite-shm && cat > checkpoints/graph.sqlite' < /tmp/graph-<stamp>.sqlite
sudo systemctl restart verity
```

That writes the file as the container's own non-root user, so permissions
stay correct.

**Instance lost.** Repeat steps 6–12 with the same role and security group,
move the Elastic IP across, then restore the checkpoint DB as above.
Everything else lives in Supabase.

## What this doesn't do

- **Single instance, single uvicorn worker.** There's no HA and no
  zero-downtime deploys; a restart is a few seconds of 502s. This is fine
  for a single-owner tool. Scaling out would need the checkpointer moved
  to Postgres first, since SQLite on local disk is per-box.
- **Secrets on the instance are root-readable.** They're fetched to tmpfs,
  but Docker stores each container's environment in its config under
  `/var/lib/docker` (root-only, encrypted EBS). Anyone with root or a disk
  snapshot can read them, so restrict who can start SSM sessions and
  snapshot the volume.
- **No WAF or CloudFront.** nginx rate limits are per source IP.
- **Owner-only Gmail with an App Password** (see `docs/auth.md` for the
  per-user OAuth migration).
- **`deploy/railway.md` predates authentication.** It has not been
  re-verified with the auth changes.
