#!/usr/bin/env bash
# Pull the latest code on the EC2 instance and rebuild the container (no SSH needed; uses SSM).
#
#   ./deploy/aws/update.sh [stack-name] [region]
set -euo pipefail

STACK="${1:-telegram-media-bot}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
INSTANCE=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)

CMD_ID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["cd /opt/telegram-media-bot","git pull --ff-only","docker compose up -d --build","docker compose ps"]' \
  --query "Command.CommandId" --output text)

aws ssm wait command-executed --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE" || true
aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE" \
  --query "[Status, StandardOutputContent, StandardErrorContent]" --output text
