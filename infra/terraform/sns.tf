resource "aws_sns_topic" "notifications" {
  name = var.sns_topic_name
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
