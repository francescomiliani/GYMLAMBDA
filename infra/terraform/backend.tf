# Il bucket e' creato da infra/bootstrap. Il nome contiene l'ID account:
# se cambia account, usare terraform init -reconfigure -backend-config="bucket=...".
terraform {
  backend "s3" {
    bucket       = "gymbooking-tfstate-163876584948"
    key          = "gymbooking/prod.tfstate"
    region       = "eu-north-1"
    profile      = "aws-personale"
    encrypt      = true
    use_lockfile = true
  }
}
