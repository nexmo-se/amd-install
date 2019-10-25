
import subprocess
import logging
import boto3
import botocore
import json
import requests
import os.path
from os import path
import sys
import time
import docker
import base64
from botocore.exceptions import ClientError

AWS_KEY=""
AWS_SECRET=""
S3_BUCKET_NAME= ""
DB_TABLE=""
REGION=""

#this do not need to changed
ECS_CLUSTER = 'amd-cluster'
ECS_SERVICE = 'amd-service'
LOCAL_REPOSITORY = 'amd'

ENV="prod"
MODEL="https://vonage-amd.s3.amazonaws.com/models/export.pkl"

import os
os.environ["AWS_ACCESS_KEY_ID"] = AWS_KEY
os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET

def process_command(cmd):
	output = subprocess.run(cmd, shell=True)
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
	"""
	Checks to verify existance of bucket
	:param bucket_name: Bucket name to verify existance of

	"""

	s3 = boto3.resource('s3')
	return s3.Bucket(bucket_name) in s3.buckets.all()

def download_model(url):
	"""
	Downloads ML Model from Vonage owned S3 Bucket
	:param url: Vonage s3 url
	"""

	print('downloading model...')
	r = requests.get(url)

	with open('export.pkl', 'wb') as f:
		f.write(r.content)
	print("model downloaded")

def upload_file_to_s3(file_name, bucket, object_name=None):
	"""Upload a file to an S3 bucket

	:param file_name: File to upload
	:param bucket: Bucket to upload to
	:param object_name: S3 object name. If not specified then file_name is used
	:return: True if file was uploaded, else False
	"""

	print("uploading file {} to bucket {}".format(file_name,bucket))
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

	print(response)
	print("file uploaded")
	return True

def get_s3_path(object_name, bucket):
	"""
	Returns the full URL of the requested object
	:param object_name: the full path to the object on S3
	:param bucket: name of S3 bucket
	"""
	s3_client = boto3.client('s3')
	bucket_location = s3_client.get_bucket_location(Bucket=bucket)
	url = "https://s3.{0}.amazonaws.com/{1}/{2}".format(bucket_location['LocationConstraint'], bucket, object_name)
	return url

def create_db(table_name,region):
	"""
	Creates dynamodb table
	:param table_name: Name of Table
	:param region: current region
	"""
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
	"""
	Inserts pre-defined items into Table
	:param table_name: Name of Table
	:param region: current region
	:param bucket_name: Name of S3 bucket
	:param model_path: local path of ML Model (downloaded from Vonage S3)
	"""
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
		upload_file_to_s3("export.pkl",S3_BUCKET_NAME,object_name="models/export.pkl")

		#create and update dynamodb
		create_db(DB_TABLE, REGION)
		update_table(DB_TABLE, REGION, S3_BUCKET_NAME, model_path="models/export.pkl")
	else:
		print("Error: could not find downloaded model")


def create_ecr_repo(repositoryName):
	"""
	Creates ECR repo
	:param repositoryName: Name of ECR repo
	"""
	client = boto3.client('ecr',region_name=REGION)
	response = client.create_repository(
	    repositoryName=repositoryName,
	    imageTagMutability='MUTABLE'
	)
	print("create_ecr_repo {}".format(response))

def login_ecr():
	process_command("aws ecr get-login --no-include-email  --region {} | sh".format(REGION))

def update_api_stack_yml():
	YAML_API_PATH = "cloud_formation_stacks/api.yml"
	image_arn = get_ecr_image()

	if image_arn is None:
		return

	if not os.path.exists(YAML_API_PATH):
		print("ERROR: YAML file does not exist")
		return False

	with open(YAML_API_PATH) as f:
		file = f.read()

	text=file.replace('<INSTANCE_NAME>', LOCAL_REPOSITORY)
	text=text.replace('<IMAGE>', image_arn)
	text=text.replace('<AWS_KEY>', AWS_KEY)
	text=text.replace('<AWS_SECRET>', AWS_SECRET)
	text=text.replace('<REGION>', REGION)
	text=text.replace('<DB_TABLE>', DB_TABLE)
	text=text.replace('<ENV>', ENV)

	with open(YAML_API_PATH, "w") as f:
		f.write(text)

	return True

def deploy_cloud_formation_scripts(stack_path, stack_name, capabilities=None):

	upload_file_to_s3(stack_path,S3_BUCKET_NAME,stack_path)
	stack_url = get_s3_path(stack_path,S3_BUCKET_NAME)

	client_cf = boto3.client('cloudformation', aws_access_key_id=AWS_KEY,
	aws_secret_access_key=AWS_SECRET, region_name=REGION)

	params = {
	    'StackName': stack_name,
	    'TemplateURL': stack_url,
	}

	if capabilities is not None:
		params["Capabilities"] = capabilities

	try:
	    if stack_exists(stack_name, client_cf):
	        print('Updating {}'.format(stack_name))
	        stack_result = client_cf.update_stack(**params)
	        waiter = client_cf.get_waiter('stack_update_complete')
	    else:
	        print('Creating {}'.format(stack_name))
	        stack_result = client_cf.create_stack(**params)
	        waiter = client_cf.get_waiter('stack_create_complete')
	    print("...waiting for stack to be ready...")
	    waiter.wait(StackName=stack_name)
	except botocore.exceptions.ClientError as ex:
	    error_message = ex.response['Error']['Message']
	    if error_message == 'No updates are to be performed.':
	        print("No changes")
	    else:
	        raise
	else:
	    print(client_cf.describe_stacks(StackName=stack_result['StackId']))

def stack_exists(stack_name, client_cf):
    stacks = client_cf.list_stacks()['StackSummaries']
    for stack in stacks:
        if stack['StackStatus'] == 'DELETE_COMPLETE':
            continue
        if stack_name == stack['StackName']:
            return True
    return False

def deploy_cf_stacks():
	deploy_cloud_formation_scripts('cloud_formation_stacks/vpc.yml', 'amd-vpc')
	deploy_cloud_formation_scripts('cloud_formation_stacks/iam.yml', 'amd-iam', ['CAPABILITY_IAM'])
	deploy_cloud_formation_scripts('cloud_formation_stacks/app-cluster.yml', 'amd-Cluster')
	update_api_stack_yml()
	deploy_cloud_formation_scripts('cloud_formation_stacks/api.yml', 'amd-api')

def deploy_docker_image():
	print("Login To AWS ECR")
	login_ecr()

	print("Creating Respoitiory on ECR...")
	create_ecr_repo(LOCAL_REPOSITORY)

	# build Docker image
	docker_client = docker.from_env()
	print("building docker file ...")

	image = docker_client.images.get(LOCAL_REPOSITORY)

	print("Login to ECR")
	# get AWS ECR login token
	ecr_client = boto3.client(
		'ecr', aws_access_key_id=AWS_KEY,
		aws_secret_access_key=AWS_SECRET, region_name=REGION)

	ecr_credentials = (
		ecr_client
		.get_authorization_token()
		['authorizationData'][0])

	ecr_username = 'AWS'

	ecr_password = (
		base64.b64decode(ecr_credentials['authorizationToken'])
		.replace(b'AWS:', b'')
		.decode('utf-8'))

	print("ecr_credentials {}".format(ecr_credentials))
	ecr_url = ecr_credentials['proxyEndpoint']
	print("ecr_url {}".format(ecr_url))

	print("Login to Docker")
	# get Docker to login/authenticate with ECR
	docker_login = docker_client.login(
		username=ecr_username, password=ecr_password, registry=ecr_url)
	print("docker_login {}".format(docker_login))

	print("Tagging image..")
	# tag image for AWS ECR
	ecr_repo_name = '{}/{}'.format(
		ecr_url.replace('https://', ''), LOCAL_REPOSITORY)

	image.tag(ecr_repo_name, tag='latest')

	print("pushing image to AWS ECR")
	# push image to AWS ECR
	push_log = docker_client.images.push(ecr_repo_name, tag='latest')
	print(push_log)

def get_ecr_image():
	ecr_client = boto3.client(
		'ecr', aws_access_key_id=AWS_KEY,
		aws_secret_access_key=AWS_SECRET, region_name=REGION)

	response = ecr_client.describe_repositories(
	    repositoryNames=[
	        LOCAL_REPOSITORY,
	    ]
	)
	try:
		print(response)
		return response["repositories"][0]["repositoryUri"]
	except:
		print("Unable to fetch image")
		return None

def main():
	print("="*60)
	print("To get started, run the first command `initialize_aws_settings`. This will provision a S3 bucket as well as a DyanomDB Table. When this is completed, run `deploy_docker_image` to build the Docker Image (Note **) You will have to build the docker image manually. Finally run Deploy CloudFormation Stacks to deploy to your AWS instance")
	print("="*60)

	switcher = {
		  1: initialize_aws_settings,
		  2: deploy_docker_image,
		  3: deploy_cf_stacks
		  }
	while(True):
		s_choice = input('''
		1: Initialize AWS Settings
		2: Deploy Docker Image
		3: Deploy CloudFormation Stacks
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
