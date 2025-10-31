#!/bin/bash

# EBS Cost Optimizer Cleanup Script
# This script removes all AWS resources created by the deployment

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
FUNCTION_NAME="EBSCostOptimizer"
ROLE_NAME="EBSCostOptimizerRole"
SNS_TOPIC_NAME="EBS-Cost-Optimizer-Notifications"
RULE_NAME="EBSCostOptimizerDailySchedule"
REGION="us-east-1"

echo -e "${RED}========================================${NC}"
echo -e "${RED}EBS Cost Optimizer Cleanup${NC}"
echo -e "${RED}========================================${NC}"
echo -e "${YELLOW}This will delete all resources created by the deployment.${NC}"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

# Get AWS Account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Remove EventBridge rule targets
echo -e "\n${YELLOW}Removing EventBridge rule targets...${NC}"
aws events remove-targets \
  --rule $RULE_NAME \
  --ids 1 \
  --region $REGION \
  2>/dev/null || echo "No targets to remove"

# Delete EventBridge rule
echo -e "${YELLOW}Deleting EventBridge rule...${NC}"
aws events delete-rule \
  --name $RULE_NAME \
  --region $REGION \
  2>/dev/null || echo "Rule not found"

# Remove Lambda permission
echo -e "${YELLOW}Removing Lambda permissions...${NC}"
aws lambda remove-permission \
  --function-name $FUNCTION_NAME \
  --statement-id EventBridgeInvoke \
  --region $REGION \
  2>/dev/null || echo "Permission not found"

# Delete Lambda function
echo -e "${YELLOW}Deleting Lambda function...${NC}"
aws lambda delete-function \
  --function-name $FUNCTION_NAME \
  --region $REGION \
  2>/dev/null || echo "Function not found"

# Delete CloudWatch Log Group
echo -e "${YELLOW}Deleting CloudWatch log group...${NC}"
aws logs delete-log-group \
  --log-group-name "/aws/lambda/${FUNCTION_NAME}" \
  --region $REGION \
  2>/dev/null || echo "Log group not found"

# Delete CloudWatch Dashboard
echo -e "${YELLOW}Deleting CloudWatch dashboard...${NC}"
aws cloudwatch delete-dashboards \
  --dashboard-names EBSCostOptimizer \
  --region $REGION \
  2>/dev/null || echo "Dashboard not found"

# Delete SNS topic
echo -e "${YELLOW}Deleting SNS topic...${NC}"
SNS_TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT_ID}:${SNS_TOPIC_NAME}"
aws sns delete-topic \
  --topic-arn $SNS_TOPIC_ARN \
  --region $REGION \
  2>/dev/null || echo "Topic not found"

# Delete IAM role policy
echo -e "${YELLOW}Deleting IAM role policy...${NC}"
aws iam delete-role-policy \
  --role-name $ROLE_NAME \
  --policy-name EBSCostOptimizerPolicy \
  2>/dev/null || echo "Policy not found"

# Delete IAM role
echo -e "${YELLOW}Deleting IAM role...${NC}"
aws iam delete-role \
  --role-name $ROLE_NAME \
  2>/dev/null || echo "Role not found"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Cleanup completed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\nAll resources have been removed."
