# Deploying to a VPS at parts.shep.rip

Result: `https://parts.shep.rip` served through Cloudflare, with the app running in
Docker on your VPS and **no inbound ports open** on the VPS at all. Cloudflare handles
TLS, the tunnel carries traffic out from the VPS to Cloudflare, and a rate-limit rule at
the edge backs up the app's own limits.

```
visitor ──HTTPS──> Cloudflare (parts.shep.rip) ──tunnel──> cloudflared ──> parts:8000
                     TLS, WAF, rate limit           outbound only        docker network
```

## Prerequisites

* `shep.rip` is on Cloudflare (nameservers pointed at Cloudflare).
* Docker and the compose plugin on the VPS (`curl -fsSL https://get.docker.com | sh`).
* Git clone of this repo on the VPS.

## Step 1: create the tunnel (pick one route)

### Route A: dashboard (5 minutes, no tooling)

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**
   → *Cloudflared* → name it `parts`.
2. On the *Install connector* screen ignore the install commands, just copy the long
   **token** from the docker command shown (the string after `--token`).
3. **Public Hostname** tab → **Add a public hostname**:
   * Subdomain `parts`, Domain `shep.rip`
   * Type `HTTP`, URL `parts:8000`
4. Save. Cloudflare creates the `parts.shep.rip` DNS record for you.

### Route B: Terraform (repeatable, also adds the edge rate limit)

Needs an API token with: *Zone → DNS → Edit*, *Zone → Zone WAF → Edit*,
*Account → Cloudflare Tunnel → Edit* for `shep.rip`. See `terraform/README` in
`terraform/main.tf` header for the exact steps; the short version:

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in zone_id and account_id
export CLOUDFLARE_API_TOKEN=...                # never commit this
terraform init && terraform apply
terraform output -raw tunnel_token             # goes into .env.prod
```

## Step 2: run it

```bash
cd pc-parts-scraper/deploy
cp .env.prod.example .env.prod
nano .env.prod                                 # paste TUNNEL_TOKEN
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml logs -f cloudflared   # wait for "Registered tunnel connection"
```

Open https://parts.shep.rip. Then run the probe from the VPS to see which retailers
answer from that IP (some block datacentre ranges):

```bash
docker compose -f docker-compose.prod.yml exec parts python -m scripts.probe "rtx 4070"
```

## Step 3: Cloudflare settings that matter

* **SSL/TLS → Overview**: *Full* is fine (the tunnel is already encrypted end to end).
* **Speed → Optimization → Rocket Loader: OFF** for this site. The app sends a strict
  Content-Security-Policy and Rocket Loader's injected script will be blocked, breaking
  the page. Same for *Email Address Obfuscation* if you turn it on.
* **Rate limiting** (free plan allows one rule): Security → WAF → Rate limiting rules.
  Route B creates it; for Route A add one by hand: *URI Path starts with `/api/search`*,
  20 requests per 10 seconds per IP, block for 10 seconds.
* **Caching**: nothing extra needed. The app marks the page `no-cache` and static assets
  carry ETags; Cloudflare will serve `/static/*` from its cache automatically.
* **Analytics**: Cloudflare Web Analytics injects a script that the CSP blocks. If you
  want it, add `https://static.cloudflareinsights.com` to `script-src` and `connect-src`
  in `app/main.py` (`_secure`).

## Keeping the product feeds fresh

If you use affiliate feeds (see the main README), import them daily. Add to the VPS
crontab (`crontab -e`), adjusting the path:

```cron
# Import affiliate product feeds at 05:20 every morning
20 5 * * * cd /home/YOU/pc-parts-scraper/deploy && docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T parts python -m scripts.import_feeds >> /var/log/parts-feeds.log 2>&1
```

Feeds usually refresh once or twice a day, so more often than that gains nothing. Check
it worked with:

```bash
docker compose -f docker-compose.prod.yml exec parts python -m scripts.import_feeds --list
```

`feeds.json` must be readable inside the container. Either bake it in by placing it in
the repo root before `--build`, or mount it by adding to the `parts` service:

```yaml
    volumes:
      - parts-data:/srv/data
      - ../feeds.json:/srv/feeds.json:ro
```

## Updating

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

Data (cache, price history) lives in the `parts-data` volume and survives rebuilds.

## Backups

The whole state is one SQLite file:

```bash
docker compose -f docker-compose.prod.yml exec parts sqlite3 /srv/data/cache.sqlite3 ".backup /srv/data/backup.sqlite3" 2>/dev/null \
  || docker compose -f docker-compose.prod.yml cp parts:/srv/data/cache.sqlite3 ./backup-$(date +%F).sqlite3
```

## If something is wrong

| Symptom | Check |
|---------|-------|
| 502 / "Bad gateway" from Cloudflare | `docker compose logs cloudflared` — token wrong or app unhealthy |
| Page loads but is blank / unstyled | Rocket Loader or another injected script; turn it off |
| Everyone shares one rate limit | `--proxy-headers` missing from the `parts` command |
| Retailers all "blocked" | Datacentre IP; try `ENABLED_RETAILERS` to drop the worst offenders or run the scraper from home |
