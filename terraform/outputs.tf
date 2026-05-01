###############################################################
# outputs.tf — values printed after terraform apply
###############################################################

output "ec2_public_ip" {
  description = "EC2 public IP — SSH in with: ssh -i your-key.pem ubuntu@<this_ip>"
  value       = aws_instance.ner_pipeline.public_ip
}

output "ec2_public_dns" {
  description = "EC2 public DNS hostname"
  value       = aws_instance.ner_pipeline.public_dns
}

output "s3_bucket_name" {
  description = "S3 bucket name for pipeline data"
  value       = aws_s3_bucket.main.bucket
}

output "s3_bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.main.arn
}

output "dashboard_url" {
  description = "Streamlit dashboard URL (available after pipeline runs)"
  value       = "http://${aws_instance.ner_pipeline.public_ip}:8501"
}

output "ssh_command" {
  description = "SSH command to connect to EC2"
  value       = "ssh -i ${var.key_pair_name}.pem ubuntu@${aws_instance.ner_pipeline.public_ip}"
}
