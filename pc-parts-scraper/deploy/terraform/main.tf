# Cloudflare side of pcparts.shep.rip: a named tunnel, its ingress, the DNS record and an
# edge rate-limit on the search API.
#
# Usage
#   1. Create an API token (My Profile > API Tokens > Create Token > Custom) with:
#        Zone    | DNS               | Edit     (shep.rip)
#        Zone    | Zone WAF          | Edit     (shep.rip)
#        Account | Cloudflare Tunnel | Edit
#   2. cp terraform.tfvars.example terraform.tfvars  and fill in zone_id / account_id
#      (both are on the shep.rip overview page in the dashboard, right-hand column).
#   3. export CLOUDFLARE_API_TOKEN=...   (env var, never in a file that is committed)
#   4. terraform init && terraform apply
#   5. terraform output -raw tunnel_token   ->  TUNNEL_TOKEN in deploy/.env.prod
#
# The state file contains the tunnel secret; keep it private (it is git-ignored).

terraform {
  required_version = ">= 1.5"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "cloudflare" {}

# --- Tunnel -------------------------------------------------------------------------

resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_tunnel" "pcparts" {
  account_id = var.account_id
  name       = var.tunnel_name
  secret     = random_id.tunnel_secret.b64_std
  config_src = "cloudflare"
}

resource "cloudflare_tunnel_config" "pcparts" {
  account_id = var.account_id
  tunnel_id  = cloudflare_tunnel.pcparts.id

  config {
    ingress_rule {
      hostname = "${var.subdomain}.${var.zone_name}"
      service  = "http://${var.origin_service}"
      origin_request {
        connect_timeout = "10s"
        http2_origin    = false
      }
    }
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# --- DNS ----------------------------------------------------------------------------

resource "cloudflare_record" "pcparts" {
  zone_id = var.zone_id
  name    = var.subdomain
  type    = "CNAME"
  content = "${cloudflare_tunnel.pcparts.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "PC parts price search, served via Cloudflare Tunnel"
}

# --- Edge rate limit on the search API ----------------------------------------------
# Second layer on top of the app's own per-IP and global limits. Free plan: one rule,
# 10 second period, 10 second mitigation.

resource "cloudflare_ruleset" "rate_limit" {
  zone_id     = var.zone_id
  name        = "pcparts rate limits"
  description = "Slow down abusive clients before they reach the origin"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules {
    description = "Search API: ${var.search_requests_per_10s} requests per 10s per IP"
    expression  = "(http.host eq \"${var.subdomain}.${var.zone_name}\" and starts_with(http.request.uri.path, \"/api/search\"))"
    action      = "block"
    enabled     = true
    ratelimit {
      characteristics     = ["ip.src", "cf.colo.id"]
      period              = 10
      requests_per_period = var.search_requests_per_10s
      mitigation_timeout  = 10
    }
  }
}
