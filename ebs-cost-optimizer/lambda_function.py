import json
import boto3
import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple

# Initialize AWS clients
ec2_client = boto3.client('ec2')
sns_client = boto3.client('sns')
cloudwatch_client = boto3.client('cloudwatch')

# Environment variables
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')
AUTO_DELETE = os.environ.get('AUTO_DELETE', 'false').lower() == 'true'
VOLUME_AGE_DAYS = int(os.environ.get('VOLUME_AGE_DAYS', '7'))
DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() == 'true'

# EBS pricing per GB-month (approximate for gp3 in us-east-1)
EBS_COST_PER_GB = 0.08


def lambda_handler(event, context):
    """
    Main Lambda handler function to identify and manage unattached EBS volumes.
    """
    print(f"Starting EBS Cost Optimizer at {datetime.now(timezone.utc)}")
    print(f"Configuration: AUTO_DELETE={AUTO_DELETE}, DRY_RUN={DRY_RUN}, VOLUME_AGE_DAYS={VOLUME_AGE_DAYS}")
    
    try:
        # Get all EBS volumes
        unattached_volumes = get_unattached_volumes()
        
        if not unattached_volumes:
            print("No unattached volumes found.")
            send_notification("EBS Cost Optimizer - No Action Needed", 
                            "All EBS volumes are currently attached. No orphaned volumes detected.")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No unattached volumes found'})
            }
        
        # Calculate costs and prepare report
        total_size, total_cost, volume_details = calculate_costs(unattached_volumes)
        
        # Log metrics to CloudWatch
        log_metrics_to_cloudwatch(len(unattached_volumes), total_size, total_cost)
        
        # Delete volumes if auto-delete is enabled
        deleted_volumes = []
        if AUTO_DELETE and not DRY_RUN:
            deleted_volumes = delete_volumes(unattached_volumes)
        
        # Send detailed report via SNS
        send_detailed_report(unattached_volumes, total_size, total_cost, 
                           volume_details, deleted_volumes)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'EBS Cost Optimizer completed successfully',
                'unattached_volumes': len(unattached_volumes),
                'total_size_gb': total_size,
                'monthly_cost': total_cost,
                'deleted_volumes': len(deleted_volumes)
            })
        }
        
    except Exception as e:
        error_message = f"Error in EBS Cost Optimizer: {str(e)}"
        print(error_message)
        send_notification("EBS Cost Optimizer - ERROR", error_message)
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def get_unattached_volumes() -> List[Dict]:
    """
    Retrieve all unattached EBS volumes from all regions.
    """
    unattached_volumes = []
    
    # Get all available regions
    regions = [region['RegionName'] for region in ec2_client.describe_regions()['Regions']]
    print(f"Scanning {len(regions)} regions for unattached volumes...")
    
    for region in regions:
        try:
            regional_ec2 = boto3.client('ec2', region_name=region)
            
            # Describe all volumes with 'available' state (unattached)
            response = regional_ec2.describe_volumes(
                Filters=[{'Name': 'status', 'Values': ['available']}]
            )
            
            for volume in response['Volumes']:
                volume_age = get_volume_age(volume['CreateTime'])
                
                # Only include volumes older than specified days
                if volume_age >= VOLUME_AGE_DAYS:
                    volume_info = {
                        'VolumeId': volume['VolumeId'],
                        'Size': volume['Size'],
                        'VolumeType': volume['VolumeType'],
                        'CreateTime': volume['CreateTime'].isoformat(),
                        'AvailabilityZone': volume['AvailabilityZone'],
                        'Region': region,
                        'Age': volume_age,
                        'State': volume['State'],
                        'Encrypted': volume.get('Encrypted', False),
                        'Tags': {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
                    }
                    unattached_volumes.append(volume_info)
                    print(f"Found unattached volume: {volume['VolumeId']} in {region} "
                          f"({volume['Size']}GB, {volume_age} days old)")
            
        except Exception as e:
            print(f"Error scanning region {region}: {str(e)}")
            continue
    
    print(f"Total unattached volumes found: {len(unattached_volumes)}")
    return unattached_volumes


def get_volume_age(create_time: datetime) -> int:
    """
    Calculate the age of a volume in days.
    """
    if create_time.tzinfo is None:
        create_time = create_time.replace(tzinfo=timezone.utc)
    
    age = datetime.now(timezone.utc) - create_time
    return age.days


def calculate_costs(volumes: List[Dict]) -> Tuple[int, float, List[Dict]]:
    """
    Calculate total size and estimated monthly costs for unattached volumes.
    """
    total_size = 0
    volume_details = []
    
    for volume in volumes:
        size = volume['Size']
        monthly_cost = size * EBS_COST_PER_GB
        
        total_size += size
        volume_details.append({
            'VolumeId': volume['VolumeId'],
            'Size': size,
            'Cost': monthly_cost,
            'Region': volume['Region'],
            'Age': volume['Age'],
            'Type': volume['VolumeType']
        })
    
    total_cost = total_size * EBS_COST_PER_GB
    
    print(f"Total Size: {total_size}GB, Estimated Monthly Cost: ${total_cost:.2f}")
    return total_size, total_cost, volume_details


def delete_volumes(volumes: List[Dict]) -> List[str]:
    """
    Delete unattached volumes (only if AUTO_DELETE is enabled and DRY_RUN is false).
    """
    deleted_volumes = []
    
    print(f"Attempting to delete {len(volumes)} volumes...")
    
    for volume in volumes:
        try:
            regional_ec2 = boto3.client('ec2', region_name=volume['Region'])
            
            # Check if volume has a 'DoNotDelete' tag
            if 'DoNotDelete' in volume['Tags'] and volume['Tags']['DoNotDelete'].lower() == 'true':
                print(f"Skipping {volume['VolumeId']}: Has DoNotDelete tag")
                continue
            
            # Delete the volume
            regional_ec2.delete_volume(VolumeId=volume['VolumeId'])
            deleted_volumes.append(volume['VolumeId'])
            print(f"Deleted volume: {volume['VolumeId']} in {volume['Region']}")
            
        except Exception as e:
            print(f"Error deleting volume {volume['VolumeId']}: {str(e)}")
            continue
    
    print(f"Successfully deleted {len(deleted_volumes)} volumes")
    return deleted_volumes


def log_metrics_to_cloudwatch(volume_count: int, total_size: int, total_cost: float):
    """
    Log custom metrics to CloudWatch for monitoring and alerting.
    """
    try:
        namespace = 'EBS/CostOptimizer'
        
        cloudwatch_client.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    'MetricName': 'UnattachedVolumeCount',
                    'Value': volume_count,
                    'Unit': 'Count',
                    'Timestamp': datetime.now(timezone.utc)
                },
                {
                    'MetricName': 'UnattachedVolumeSizeGB',
                    'Value': total_size,
                    'Unit': 'Gigabytes',
                    'Timestamp': datetime.now(timezone.utc)
                },
                {
                    'MetricName': 'EstimatedMonthlyCost',
                    'Value': total_cost,
                    'Unit': 'None',
                    'Timestamp': datetime.now(timezone.utc)
                }
            ]
        )
        print(f"Logged metrics to CloudWatch namespace: {namespace}")
        
    except Exception as e:
        print(f"Error logging metrics to CloudWatch: {str(e)}")


def send_detailed_report(volumes: List[Dict], total_size: int, total_cost: float,
                        volume_details: List[Dict], deleted_volumes: List[str]):
    """
    Send a detailed HTML report via SNS email.
    """
    subject = f"EBS Cost Optimizer Report - {len(volumes)} Unattached Volumes Found"
    
    # Build HTML email body
    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .header {{ background-color: #232f3e; color: white; padding: 20px; text-align: center; }}
            .summary {{ background-color: #f4f4f4; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            .metric {{ display: inline-block; margin: 10px 20px; }}
            .metric-value {{ font-size: 24px; font-weight: bold; color: #ff9900; }}
            .metric-label {{ font-size: 14px; color: #666; }}
            table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
            th {{ background-color: #232f3e; color: white; padding: 12px; text-align: left; }}
            td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
            tr:hover {{ background-color: #f5f5f5; }}
            .footer {{ margin-top: 30px; padding: 20px; background-color: #f4f4f4; font-size: 12px; color: #666; }}
            .warning {{ background-color: #fff3cd; padding: 10px; border-left: 4px solid #ffc107; margin: 20px 0; }}
            .success {{ background-color: #d4edda; padding: 10px; border-left: 4px solid #28a745; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>EBS Cost Optimizer Report</h1>
            <p>Scan Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        </div>
        
        <div class="summary">
            <div class="metric">
                <div class="metric-value">{len(volumes)}</div>
                <div class="metric-label">Unattached Volumes</div>
            </div>
            <div class="metric">
                <div class="metric-value">{total_size} GB</div>
                <div class="metric-label">Total Size</div>
            </div>
            <div class="metric">
                <div class="metric-value">${total_cost:.2f}</div>
                <div class="metric-label">Monthly Cost</div>
            </div>
        </div>
    """
    
    if DRY_RUN:
        html_body += """
        <div class="warning">
            <strong>DRY RUN MODE:</strong> No volumes were deleted. Set DRY_RUN=false to enable deletion.
        </div>
        """
    
    if deleted_volumes:
        html_body += f"""
        <div class="success">
            <strong>Success:</strong> {len(deleted_volumes)} volumes were automatically deleted.
        </div>
        """
    
    html_body += """
        <h2>Volume Details</h2>
        <table>
            <thead>
                <tr>
                    <th>Volume ID</th>
                    <th>Region</th>
                    <th>Size (GB)</th>
                    <th>Type</th>
                    <th>Age (Days)</th>
                    <th>Monthly Cost</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    """
    
    for vol in volume_details:
        status = "DELETED" if vol['VolumeId'] in deleted_volumes else "AVAILABLE"
        status_color = "#28a745" if status == "DELETED" else "#ffc107"
        
        html_body += f"""
                <tr>
                    <td>{vol['VolumeId']}</td>
                    <td>{vol['Region']}</td>
                    <td>{vol['Size']}</td>
                    <td>{vol['Type']}</td>
                    <td>{vol['Age']}</td>
                    <td>${vol['Cost']:.2f}</td>
                    <td style="color: {status_color}; font-weight: bold;">{status}</td>
                </tr>
        """
    
    html_body += """
            </tbody>
        </table>
        
        <div class="footer">
            <p><strong>Configuration:</strong></p>
            <ul>
                <li>Auto Delete: {auto_delete}</li>
                <li>Dry Run: {dry_run}</li>
                <li>Minimum Volume Age: {age} days</li>
            </ul>
            <p>This is an automated report from EBS Cost Optimizer Lambda function.</p>
        </div>
    </body>
    </html>
    """.format(
        auto_delete="Enabled" if AUTO_DELETE else "Disabled",
        dry_run="Enabled" if DRY_RUN else "Disabled",
        age=VOLUME_AGE_DAYS
    )
    
    send_notification(subject, html_body, is_html=True)


def send_notification(subject: str, message: str, is_html: bool = False):
    """
    Send notification via SNS.
    """
    if not SNS_TOPIC_ARN:
        print("SNS_TOPIC_ARN not configured. Skipping notification.")
        return
    
    try:
        message_attributes = {}
        if is_html:
            message_attributes = {
                'Content-Type': {
                    'DataType': 'String',
                    'StringValue': 'text/html'
                }
            }
        
        response = sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message,
            MessageAttributes=message_attributes
        )
        
        print(f"SNS notification sent. MessageId: {response['MessageId']}")
        
    except Exception as e:
        print(f"Error sending SNS notification: {str(e)}")


# For local testing
if __name__ == "__main__":
    test_event = {}
    test_context = {}
    result = lambda_handler(test_event, test_context)
    print(json.dumps(result, indent=2))
