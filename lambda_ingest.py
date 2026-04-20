"""
AWS Lambda entry point for lightweight news ingestion.

This keeps the RSS fetch on Lambda while the heavy ML stages stay on EC2/Docker.
Set these Lambda environment variables:
  LOCAL_MODE=false
  S3_BUCKET=<your-bucket>
  AWS_DEFAULT_REGION=us-east-1
"""

from ingest import run


def handler(event, context):
    run()
    return {
        "statusCode": 200,
        "body": "ingest complete",
    }
