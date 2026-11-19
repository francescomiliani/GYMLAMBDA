# I pacchetti si costruiscono prima del plan con infra/scripts/tf.sh (o a mano):
#   python3 scripts/build_bot_package.py --with-deps
#   python3 scripts/build_bot_package.py --legacy --with-deps

data "archive_file" "bot" {
  type        = "zip"
  source_dir  = "${path.module}/../../build/bot-package"
  output_path = "${path.module}/../../build/bot.zip"
}

data "archive_file" "legacy" {
  type        = "zip"
  source_dir  = "${path.module}/../../build/legacy-package"
  output_path = "${path.module}/../../build/legacy.zip"
}
