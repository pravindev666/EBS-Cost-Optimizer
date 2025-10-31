#!/bin/bash

# EBS Cost Optimizer Deployment Script
# This script automates the deployment of all AWS resources

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
FUNCTION_NAME="EBSCostOptimizer"
ROLE_NAME="EBSCostOptimizerRole"
SNS_TOPIC_NAME="EBS-Cost-Optimizer-Notifications"
RULE_NAME="EBSCostOptimizerDailySchedule"
REGION="us-east-1"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}EBS Cost Optimizer Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

# Get AWS Account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${YELLOW}AWS Account ID: ${ACCOUNT_ID}${NC}"

# Step 1: Create IAM Role
echo -e "\n${YELLOW}Step 1: Creating IAM Role...${NC}"
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)

aws iam create-role \
  --role-name $ROLE_NAME \
  --assume-role-policy-document "$TRUST_POLICY" \
  --description "IAM role for EBS Cost Optimizer Lambda function" \
  2>/dev/null || echo "Role already exists"

# Attach IAM policy
echo -e "${YELLOW}Attaching IAM policy...${NC}"
aws iam put-role-policy \
  --role-name $ROLE_NAME \
  --policy-name EBSCostOptimizerPolicy \
  --policy-document file://iam_policy.json

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo -e "${GREEN}IAM Role created: ${ROLE_ARN}${NC}"

# Step 2: Create SNS Topic
echo -e "\n${YELLOW}Step 2: Creating SNS Topic...${NC}"
SNS_TOPIC_ARN=$(aws sns create-topic \
  --name $SNS_TOPIC_NAME \
  --region $REGION \
  --query 'TopicArn' \
  --output text)

echo -e "${GREEN}SNS Topic created: ${SNS_TOPIC_ARN}${NC}"

# Subscribe email to SNS topic
read -p "Enter email address for notifications: " EMAIL_ADDRESS
aws sns subscribe \
  --topic-arn $SNS_TOPIC_ARN \
  --protocol email \
  --notification-endpoint $EMAIL_ADDRESS \
  --region $REGION

echo -e "${YELLOW}Please check your email and confirm the SNS subscription${NC}"

# Step 3: Package Lambda function
echo -e "\n${YELLOW}Step 3: Packaging Lambda function...${NC}"
zip -r lambda_function.zip lambda_function.py
echo -e "${GREEN}Lambda package created${NC}"

# Step 4: Create Lambda function
echo -e "\n${YELLOW}Step 4: Creating Lambda function...${NC}"
sleep 10  # Wait for IAM role to propagate

aws lambda create-function \
  --function-name $FUNCTION_NAME \
  --runtime python3.11 \
  --role $ROLE_ARN \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://lambda_function.zip \
  --timeout 300 \
  --memory-size 256 \
  --region $REGION \
  --environment "Variables={
    SNS_TOPIC_ARN=${SNS_TOPIC_ARN},
    AUTO_DELETE=false,
    DRY_RUN=true,
    VOLUME_AGE_DAYS=7
  }" \
  --description "Automated EBS cost optimizer to identify and manage unattached volumes" \
  2>/dev/null || {
    echo -e "${YELLOW}Function exists, updating code...${NC}"
    aws lambda update-function-code \
      --function-name $FUNCTION_NAME \
      --zip-file fileb://lambda_function.zip \
      --region $REGION
  }

LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"
echo -e "${GREEN}Lambda function created: ${LAMBDA_ARN}${NC}"

# Step 5: Create EventBridge rule
echo -e "\n${YELLOW}Step 5: Creating EventBridge schedule...${NC}"
aws events put-rule \
  --name $RULE_NAME \
  --schedule-expression "cron(0 9 * * ? *)" \
  --state ENABLED \
  --description "Triggers EBS Cost Optimizer daily at 9 AM UTC" \
  --region $REGION

# Add Lambda permission for EventBridge
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id EventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}" \
  --region $REGION \
  2>/dev/null || echo "Permission already exists"

# Add target to EventBridge rule
aws events put-targets \
  --rule $RULE_NAME \
  --targets "Id=1,Arn=${LAMBDA_ARN}" \
  --region $REGION

echo -e "${GREEN}EventBridge rule created and configured${NC}"

# Step 6: Create CloudWatch dashboard
echo -e "\n${YELLOW}Step 6: Creating CloudWatch dashboard...${NC}"
DASHBOARD_BODY=$(cat <<EOF
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["EBS/CostOptimizer", "UnattachedVolumeCount"],
          [".", "UnattachedVolumeSizeGB"],
          [".", "EstimatedMonthlyCost"]
        ],
        "period": 86400,
        "stat": "Average",
        "region": "${REGION}",
        "title": "EBS Cost Optimizer Metrics",
        "yAxis": {
          "left": {
            "min": 0
          }
        }
      }
    }
  ]
}
EOF
)

aws cloudwatch put-dashboard \
  --dashboard-name EBSCostOptimizer \
  --dashboard-body "$DASHBOARD_BODY" \
  --region $REGION

echo -e "${GREEN}CloudWatch dashboard created${NC}"

# Cleanup
rm -f lambda_function.zip

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment completed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\n${YELLOW}Next steps:${NC}"
echo "1. Confirm SNS email subscription"
echo "2. Test Lambda function: aws lambda invoke --function-name $FUNCTION_NAME output.json"
echo "3. View CloudWatch dashboard: https://console.aws.amazon.com/cloudwatch/home?region=${REGION}#dashboards:name=EBSCostOptimizer"
echo "4. To enable auto-deletion, update Lambda environment variable: AUTO_DELETE=true, DRY_RUN=false"
echo -e "\n${YELLOW}Important configuration:${NC}"
echo "- Current mode: DRY_RUN (no volumes will be deleted)"
echo "- Auto-delete: DISABLED"
echo "- Minimum volume age: 7 days"
echo "- Schedule: Daily at 9 AM UTC"
