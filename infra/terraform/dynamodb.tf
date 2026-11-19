resource "aws_dynamodb_table" "bot" {
  name         = var.existing.table_name
  billing_mode = "PROVISIONED"
  table_class  = "STANDARD"

  read_capacity  = 1
  write_capacity = 1

  hash_key = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "purge_at"
    enabled        = true
  }
}
