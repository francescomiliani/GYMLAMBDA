#!/usr/bin/env bash
# Inventario in SOLA LETTURA delle risorse esistenti, per compilare
# infra/terraform/terraform.tfvars. Non stampa variabili d'ambiente delle Lambda,
# ne' endpoint email delle subscription.
set -euo pipefail

PROFILE="${AWS_PROFILE:-aws-personale}"
REGION="${AWS_REGION:-eu-north-1}"
STACK="${STACK_NAME:-gymbot-telegram}"
LEGACY="${LEGACY_FUNCTION:-GymBooking-personale}"
SCHEDULE="${SCHEDULE_NAME:-gym-booking-mwf-0700}"
TOPIC="${SNS_TOPIC_NAME:-GymBooking-notifiche}"
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

echo "== Stack $STACK: risorse fisiche =="
aws_ cloudformation list-stack-resources --stack-name "$STACK" \
  --query 'StackResourceSummaries[].[LogicalResourceId,ResourceType,PhysicalResourceId]' --output text

ROLES=$(aws_ cloudformation list-stack-resources --stack-name "$STACK" \
  --query "StackResourceSummaries[?ResourceType=='AWS::IAM::Role'].PhysicalResourceId" --output text)
SCHED_ROLE=$(aws_ scheduler get-schedule --name "$SCHEDULE" --query 'Target.RoleArn' --output text | awk -F/ '{print $NF}')
LEGACY_ROLE=$(aws_ lambda get-function-configuration --function-name "$LEGACY" --query 'Role' --output text | awk -F/ '{print $NF}')

for role in $ROLES $SCHED_ROLE $LEGACY_ROLE; do
  echo; echo "== Ruolo $role =="
  echo "-- policy gestite:"; aws_ iam list-attached-role-policies --role-name "$role" --query 'AttachedPolicies[].PolicyArn' --output text
  echo "-- policy inline:";  aws_ iam list-role-policies --role-name "$role" --query 'PolicyNames' --output text
done

echo; echo "== Lambda (senza Environment) =="
for fn in $(aws_ cloudformation list-stack-resources --stack-name "$STACK" \
    --query "StackResourceSummaries[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" --output text) "$LEGACY"; do
  echo "-- $fn"
  aws_ lambda get-function-configuration --function-name "$fn" \
    --query '{Handler:Handler,Runtime:Runtime,Arch:Architectures,Mem:MemorySize,Timeout:Timeout,Role:Role,Env:keys(Environment.Variables)}' --output json
  echo "   statement id permessi resource-based:"
  aws_ lambda get-policy --function-name "$fn" --query 'Policy' --output text 2>/dev/null \
    | python3 -c 'import sys,json; t=sys.stdin.read().strip(); [print("   ", s["Sid"], s["Action"]) for s in (json.loads(t)["Statement"] if t else [])]' || true
  echo "   event invoke config:"
  aws_ lambda list-function-event-invoke-configs --function-name "$fn" \
    --query 'FunctionEventInvokeConfigs[].[FunctionArn,MaximumRetryAttempts,MaximumEventAgeInSeconds]' --output text 2>/dev/null || true
  echo "   versioni:"; aws_ lambda list-versions-by-function --function-name "$fn" --query 'Versions[].Version' --output text
done

echo; echo "== Schedule $SCHEDULE =="
aws_ scheduler get-schedule --name "$SCHEDULE" \
  --query '{State:State,Expr:ScheduleExpression,Tz:ScheduleExpressionTimezone,Window:FlexibleTimeWindow,Target:Target}' --output json

echo; echo "== SNS $TOPIC (solo ARN e protocollo) =="
ACCOUNT=$(aws_ sts get-caller-identity --query Account --output text)
aws_ sns list-subscriptions-by-topic --topic-arn "arn:aws:sns:$REGION:$ACCOUNT:$TOPIC" \
  --query 'Subscriptions[].[SubscriptionArn,Protocol]' --output text
