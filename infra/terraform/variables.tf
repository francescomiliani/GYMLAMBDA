variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "profile" {
  type    = string
  default = "aws-personale"
}

# --- Configurazione del bot (equivalenti dei Parameters di bot-template.yaml) ---

variable "telegram_allowed_user_id" {
  type        = string
  description = "ID Telegram autorizzato; e' anche la chat delle notifiche."
  validation {
    condition     = length(var.telegram_allowed_user_id) > 0
    error_message = "telegram_allowed_user_id non puo' essere vuoto."
  }
}

# Solo i NOMI dei parametri SSM: i valori (SecureString) non passano da Terraform (ADR 0001).
variable "ssm_telegram_bot_token" {
  type    = string
  default = "/gymbot/telegram-bot-token"
}

variable "ssm_telegram_webhook_secret" {
  type    = string
  default = "/gymbot/telegram-webhook-secret"
}

variable "ssm_sportrick_username" {
  type    = string
  default = "/gymbot/sportrick-username"
}

variable "ssm_sportrick_password" {
  type    = string
  default = "/gymbot/sportrick-password"
}

variable "ssm_google_calendar" {
  type    = string
  default = "/gymbot/google-calendar"
}

variable "booking_time" {
  type    = string
  default = "07:00"
  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", var.booking_time))
    error_message = "booking_time deve essere HH:MM."
  }
}

variable "sns_topic_name" {
  type    = string
  default = "GymBooking-notifiche"
}

variable "guard_sns_enabled" {
  type        = bool
  default     = false
  description = "Se true il guard pubblica sul topic SNS. Oggi in esercizio SNS_TOPIC_ARN e' vuota (spento)."
}

variable "notification_email" {
  type        = string
  description = "Endpoint email dell'iscrizione SNS esistente (in terraform.tfvars, non committare)."
}

# --- Schedule ---

variable "schedule_name" {
  type    = string
  default = "gym-booking-mwf-0700"
}

variable "schedule_group" {
  type    = string
  default = "default"
}

variable "scheduler_role_name" {
  type    = string
  default = "GymBooking-personale-scheduler-role"
}

# --- Nomi fisici delle risorse esistenti ---
# SAM genera nomi con suffisso casuale. Cambiarli forzerebbe la ricreazione, quindi
# vanno copiati da infra/scripts/inventory.sh in terraform.tfvars.

variable "existing" {
  type = object({
    table_name                   = string
    webhook_function_name        = string
    worker_function_name         = string
    guard_function_name          = string
    webhook_role_name            = string
    worker_role_name             = string
    guard_role_name              = string
    webhook_policy_name          = string
    worker_policy_name           = string
    guard_policy_name            = string
    legacy_role_name             = string
    legacy_policy_name           = string
    scheduler_policy_name        = string
    webhook_url_permission_id    = string
    webhook_invoke_permission_id = string
    subscription_arn             = string
  })
  description = "Nomi fisici/ID delle risorse da importare."
}

variable "legacy_function_name" {
  type    = string
  default = "GymBooking-personale"
}

# --- Controllo lista d'attesa (risorse nuove, non presenti nello stack SAM) ---

variable "waitlist_watch_name" {
  type        = string
  default     = "gymbot-waitlist-watch"
  description = "Nome di funzione, schedule e prefisso di ruoli e log group del controllo lista d'attesa."
}

variable "waitlist_watch_schedule" {
  type        = string
  default     = "cron(0/30 6-22 * * ? *)"
  description = "Ogni 30 minuti dalle 06:00 alle 22:30 (Europe/Rome): circa 1.000 invocazioni al mese, nel free tier."
}

variable "waitlist_watch_enabled" {
  type        = bool
  default     = true
  description = "La Lambda legge soltanto il portale e scrive su Telegram e Google Calendar: non prenota e non cancella."
}

variable "log_retention_days" {
  type    = number
  default = 7
}
