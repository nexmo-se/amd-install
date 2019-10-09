
import subprocess
import logging
import boto3
import json
import requests
import os.path
from os import path
import sys
import time
from botocore.exceptions import ClientError

AWS_KEY=""
AWS_SECRET=""
S3_BUCKET_NAME= ""
DB_TABLE=""
LOG_GROUP_NAME=""
INSTANCE_NAME=""

REGION="us-east-1"

#this do not need to changed
ENV="prod"
PORT="80:8080"
DOCKER_FILE="tbass134/amd"
MODEL="https://vonage-amd.s3.amazonaws.com/models/export.pkl"

import os
os.environ["AWS_ACCESS_KEY_ID"] = AWS_KEY
os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET

def process_command(cmd):
	output = subprocess.run(cmd)
	print("output {}".format(output))

def create_bucket(bucket_name, region=None):
	"""Create an S3 bucket in a specified region

	If a region is not specified, the bucket is created in the S3 default
	region (us-east-1).

	:param bucket_name: Bucket to create
	:param region: String region to create bucket in, e.g., 'us-west-2'
	:return: True if bucket created, else False
	"""
	print("Creating bucket: {} in {}".format(bucket_name,region))

	#https://github.com/elastic/elasticsearch/issues/16978
	if region == "us-east-1":
		region = None
	# Create bucket
	try:
		if region is None:
			s3_client = boto3.client('s3')
			s3_client.create_bucket(Bucket=bucket_name)
		else:
			s3_client = boto3.client('s3', region_name=region)
			location = {'LocationConstraint': region}
			s3_client.create_bucket(Bucket=bucket_name,
									CreateBucketConfiguration=location)
	except ClientError as e:
		logging.error(e)
		return False
	print("bucket created..")
	return True

def bucket_exists(bucket_name):
	s3 = boto3.resource('s3')
	return s3.Bucket(bucket_name) in s3.buckets.all()

def download_model(url):
	print('downloading model...')
	r = requests.get(url)

	with open('export.pkl', 'wb') as f:
		f.write(r.content)
	print("model downloaded")

def upload_model(file_name, bucket, object_name=None):
	"""Upload a file to an S3 bucket

	:param file_name: File to upload
	:param bucket: Bucket to upload to
	:param object_name: S3 object name. If not specified then file_name is used
	:return: True if file was uploaded, else False
	"""

	print("uploading model {} to bucket {}".format(file_name,bucket))
	# If S3 object_name was not specified, use file_name
	if object_name is None:
		object_name = file_name

	# Upload the file
	s3_client = boto3.client('s3')
	try:
		response = s3_client.upload_file(file_name, bucket, object_name)
	except ClientError as e:
		logging.error(e)
		return False

	print("model uploaded")
	return True

def create_db(table_name,region):
	print("Creating table {} in {}".format(table_name,region))
	dynamodb = boto3.resource('dynamodb', region_name=region)

	table = dynamodb.create_table(
		TableName=table_name,
		KeySchema=[
			{
				'AttributeName': 'env',
				'KeyType': 'HASH'  #Partition key
			},

		],
		AttributeDefinitions=[
			{
				'AttributeName': 'env',
				'AttributeType': 'S'
			}
		],
		ProvisionedThroughput={
			'ReadCapacityUnits': 10,
			'WriteCapacityUnits': 10
		}
	)

	print("Table status:", table.table_status)
	table.wait_until_exists()
	print("Table status:", table.table_status)

def update_table(table_name,region, bucket_name, model_path):
	print("adding items to db in table {} in region {}".format(table_name, region))
	dynamodb = boto3.resource('dynamodb', region_name=region)

	table = dynamodb.Table(table_name)
	response = table.put_item(
	   Item={
			'env': "prod",
			'bucket': bucket_name,
			'debug':True,
			'expected_prediction':[0,1],
			"model_path":model_path
		}
	)

	print("PutItem succeeded:")
	print(json.dumps(response))


def initialize_aws_settings():
	"""
	Creates a S3 bucket,
	Upload the ML model to the newly created S3 bucket
	Creates a dynamodb Table and populates the table accordingly
	"""

	#create the bucket
	create_bucket(S3_BUCKET_NAME,REGION)
	time.sleep(1)
	if not bucket_exists(S3_BUCKET_NAME):
		print("bucket not created")
		sys.exit()
	#download ML model from different S3 bucket
	download_model(MODEL)

	if path.exists("export.pkl"):
		#upload model to newly created bucket
		upload_model("export.pkl",S3_BUCKET_NAME,object_name="models/export.pkl")

		#create and update dynamodb
		create_db(DB_TABLE, REGION)
		update_table(DB_TABLE, REGION, S3_BUCKET_NAME, model_path="models/export.pkl")
	else:
		print("Error: could not find downloaded model")

def create_ec2_instance():
	print("Creating EC2 instance..")
	os.system("docker-machine create --driver amazonec2 --amazonec2-open-port 8000 --amazonec2-region {} {}".format(REGION, INSTANCE_NAME))
	print("Connectting to instance...")
	print("activate instance")

	print("="*120)

	print("Docker instance is running, however you will have to run the following commands manually")
	print("1:	run `docker-machine env {}`".format(INSTANCE_NAME))
	print("2:	then: `eval $(docker-machine env {})` ".format(INSTANCE_NAME))
	print("3:	ssh into the instance with this command `docker-machine ssh {}`".format(INSTANCE_NAME))
	print("You will then be connected into the instance")
	print("The dockerfile is private, therefore you will need to login to docker. Please ask to be added to the docker file")
	print("You will also need to setup CloudWatch logs before running the final command. Please see https://docs.google.com/document/d/1hi6fPdICSsSh1__L-9I1uXgdOfRSsTxwYp9GKH04bBQ/edit#heading=h.gc1u9cvgpw0a for more info")

	docker_command = "sudo docker run -d --log-driver=awslogs --log-opt awslogs-region={} --log-opt awslogs-group={} --log-opt awslogs-create-group=true -e AWS_KEY={} -e AWS_SECRET={} -e DB_TABLE={} -e DB_REGION={} -e ENV={} -p {} {}".format(REGION, LOG_GROUP_NAME, AWS_KEY, AWS_SECRET, DB_TABLE, REGION, ENV, PORT, DOCKER_FILE)

	print("4:	run this command `{}`".format(docker_command))
	print("="*120)
	sys.exit()

def main():
	print("="*120)
	print("To get started, run the first command `initialize_aws_settings`. This will provision a S3 bucket as well as a DyanomDB Table. When this is completed, run `create_ec2_instance` to build the EC2 instance")
	print("="*120)

	switcher = {
		  1: initialize_aws_settings,
		  2: create_ec2_instance
		  }
	while(True):
		s_choice = input('''
		1: Initalize AWS Settings
		2: Create EC2 Instance
		Enter Choice:''')

		i_choice = 0
		try:
			i_choice = float(s_choice)
		except ValueError:
			print ("Invalid Choice!!\n")
			continue

		try:
			func = switcher.get(i_choice, lambda: "Invalid Choice!!")
			func()
		except Exception as e:
			print ("Some issue happened!\n")
			print (e)
			continue

if __name__ == '__main__':
  main()
