#!/usr/bin/env bash
# Zero-downtime deploy to ECS:
#   1. register new task definitions pointing at the new image tag
#   2. run database migrations as a one-off task; stop if they fail
#   3. update services; ECS rolls tasks behind the ALB, the circuit breaker rolls back failures
# Needs: AWS_REGION CLUSTER TAG REGISTRY PRIVATE_SUBNETS APP_SG (see terraform output deploy_config)
set -euo pipefail

api_image="$REGISTRY/$CLUSTER-api:$TAG"
web_image="$REGISTRY/$CLUSTER-web:$TAG"

register() {   # family, image -> new task definition ARN (only the app container's image changes)
  local family=$1 image=$2
  aws ecs describe-task-definition --task-definition "$family" --query taskDefinition --output json \
    | jq --arg img "$image" '
        .containerDefinitions |= map(if .name != "otel" then .image = $img else . end)
        | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
              .registeredAt, .registeredBy, .deregisteredAt)' > /tmp/td.json
  aws ecs register-task-definition --cli-input-json file:///tmp/td.json --query taskDefinition.taskDefinitionArn --output text
}

echo "Registering task definitions for $TAG"
migrate_td=$(register "$CLUSTER-migrate" "$api_image")
api_td=$(register "$CLUSTER-api" "$api_image")
worker_td=$(register "$CLUSTER-worker" "$api_image")
web_td=$(register "$CLUSTER-web" "$web_image")

echo "Running migrations"
task_arn=$(aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE --task-definition "$migrate_td" \
  --network-configuration "awsvpcConfiguration={subnets=[$PRIVATE_SUBNETS],securityGroups=[$APP_SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn"
code=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" --query 'tasks[0].containers[0].exitCode' --output text)
if [ "$code" != "0" ]; then
  echo "Migration failed (exit $code). Services were not changed. Logs: /ecs/$CLUSTER/migrate"
  exit 1
fi

echo "Rolling out"
aws ecs update-service --cluster "$CLUSTER" --service api    --task-definition "$api_td"    >/dev/null
aws ecs update-service --cluster "$CLUSTER" --service worker --task-definition "$worker_td" >/dev/null
aws ecs update-service --cluster "$CLUSTER" --service web    --task-definition "$web_td"    >/dev/null
aws ecs wait services-stable --cluster "$CLUSTER" --services api worker web
echo "Deployed $TAG"
