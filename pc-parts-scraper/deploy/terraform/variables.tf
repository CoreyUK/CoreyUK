variable "account_id" {
  description = "Cloudflare account ID (dashboard: shep.rip overview, right column)."
  type        = string
}

variable "zone_id" {
  description = "Zone ID for shep.rip (same place)."
  type        = string
}

variable "zone_name" {
  type    = string
  default = "shep.rip"
}

variable "subdomain" {
  type    = string
  default = "pcparts"
}

variable "tunnel_name" {
  type    = string
  default = "pcparts"
}

variable "origin_service" {
  description = "Where cloudflared reaches the app. On the compose network it is the service name."
  type        = string
  default     = "parts:8000"
}

variable "search_requests_per_10s" {
  description = "Edge rate limit for /api/search per IP per 10 seconds."
  type        = number
  default     = 20
}
