#!/usr/bin/env bash
# Deploy (or update) the bot stack on AWS.
#
#   BOT_TOKEN=123:abc ADMIN_IDS=11111 ./deploy/aws/deploy.sh [stack-name] [region]
#
# Extra CloudFormation parameters can be passed through PARAMS, e.g.
#   PARAMS="InstanceType=t3.medium EnableLinkServer=true" ./deploy/aws/deploy.sh
set -euo pipefail

STACK="${1:-telegram-media-bot}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
: "${BOT_TOKEN:?Set BOT_TOKEN}"
: "${ADMIN_IDS:?Set ADMIN_IDS}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC2086
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file "$DIR/cloudformation.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "BotToken=$BOT_TOKEN" "AdminIds=$ADMIN_IDS" ${PARAMS:-}

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
