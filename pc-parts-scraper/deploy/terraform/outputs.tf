output "hostname" {
  value = "https://${var.subdomain}.${var.zone_name}"
}

output "tunnel_id" {
  value = cloudflare_tunnel.pcparts.id
}

output "tunnel_token" {
  description = "Paste into deploy/.env.prod as TUNNEL_TOKEN."
  value       = cloudflare_tunnel.pcparts.tunnel_token
  sensitive   = true
}
