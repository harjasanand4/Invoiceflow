# Deploying to AWS

The simplest setup that is still real: one EC2 instance running the Docker Compose stack (Postgres + API + worker), Caddy for HTTPS, and S3 for the PDFs. It takes about an hour the first time.

> **Cost:** a `t3.small` runs about US$15/month if left on. Check the current AWS free-tier terms for your account, set a billing alarm (Billing → Budgets → a $5 budget), and **stop the instance** when you're not demoing it.

## 1. Launch the server

1. EC2 → Launch instance.
   - AMI: **Ubuntu Server 24.04 LTS**
   - Type: `t3.small` (2 GB RAM; building the UI on a 1 GB `t3.micro` can run out of memory)
   - Key pair: create one and download the `.pem`
   - Security group: SSH (22) from **My IP** only; HTTP (80) and HTTPS (443) from anywhere
   - Storage: 20 GB
2. Give it a fixed address: EC2 → Elastic IPs → Allocate → Associate with the instance.

## 2. Install Docker

```bash
ssh -i your-key.pem ubuntu@YOUR_ELASTIC_IP
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit     # log out and back in so the group applies
```

## 3. Get the code and configure it

```bash
ssh -i your-key.pem ubuntu@YOUR_ELASTIC_IP
git clone https://github.com/YOUR_USERNAME/invoiceflow.git && cd invoiceflow
cp .env.example .env
nano .env
```

Set at least:

```
EXTRACTOR=hybrid
ANTHROPIC_API_KEY=sk-ant-...
BASIC_AUTH_USER=demo
BASIC_AUTH_PASSWORD=pick-a-long-random-password
```

**Always set basic auth on a public server.** Without it, anyone who finds the URL can read and approve invoices.

## 4. Start it

```bash
docker compose up -d --build
docker compose ps                  # all services "running" / "healthy"
curl localhost:8000/api/health     # {"status":"ok"}
```

Load the demo data so there's something to look at:

```bash
docker compose exec api python -m data.generate_invoices --count 80 --out /tmp/demo
docker compose exec api python -m app.cli load-pos /tmp/demo/purchase_orders.json
docker compose exec api python -m app.cli ingest /tmp/demo/pdfs
docker compose logs -f worker      # watch the worker process them (Ctrl+C to stop watching)
```

You can also drop real PDFs into `~/invoiceflow/inbox` on the server (e.g. with `scp`) and the worker picks them up.

## 5. HTTPS with a free domain

1. Get a free subdomain at [duckdns.org](https://www.duckdns.org) and point it at your Elastic IP.
2. Put that domain in `deploy/Caddyfile`.
3. Restart with the production override (adds Caddy and closes port 8000):

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

Caddy gets and renews the TLS certificate automatically. Open `https://your-subdomain.duckdns.org`.

## 6. Store PDFs in S3 (recommended)

1. S3 → Create bucket (keep **Block all public access** on).
2. IAM → Policies → Create policy → JSON → paste `deploy/s3-policy.json` with your bucket name.
3. IAM → Roles → Create role → AWS service: EC2 → attach that policy.
4. EC2 → your instance → Actions → Security → Modify IAM role → pick the role.
5. In `.env`: `STORAGE_BACKEND=s3` and `S3_BUCKET=your-bucket-name`, then `docker compose up -d`.

No access keys are stored anywhere; boto3 picks up the instance role automatically. That's the detail worth mentioning in interviews.

## 7. Backups

`deploy/backup.sh` dumps Postgres to S3. Install the AWS CLI on the server (`sudo snap install aws-cli --classic`), add `s3:PutObject` on `backups/*` to the role, and add the cron line from the top of the script.

## Updating

```bash
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

> Schema changes: the app creates missing tables on startup but does **not** alter existing ones. After changing `app/models.py`, either wipe the demo database (`docker compose down -v`, which deletes all data) or add Alembic migrations (see ROADMAP.md, stretch goals).

## Troubleshooting

| Symptom | Check |
|---|---|
| Site doesn't load | Security group allows 80/443? `docker compose ps` shows everything running? |
| Certificate errors | DuckDNS points to the Elastic IP? Port 80 open (Caddy needs it for validation)? `docker compose logs caddy` |
| Documents stuck in "queued" | `docker compose logs worker` |
| Documents "failed" | Open one in the UI: the error is shown at the top. Usually a missing or invalid API key |
| Build killed | Out of memory: use `t3.small`, or add swap |
