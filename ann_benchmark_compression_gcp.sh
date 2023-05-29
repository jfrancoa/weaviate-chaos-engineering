#!/bin/bash 

set -e

ZONE=${ZONE:-"us-central1-a"}
MACHINE_TYPE=${MACHINE_TYPE:-"n2-standard-8"}
export CLOUD_PROVIDER="gcp"
export OS="ubuntu-2304-amd64"

instance="benchmark-$(uuidgen | tr [:upper:] [:lower:])"

gcloud compute instances create $instance \
  --image-family=$OS --image-project=ubuntu-os-cloud \
  --machine-type=$MACHINE_TYPE --zone $ZONE

function cleanup {
  gcloud compute instances delete $instance --quiet --zone $ZONE
}
trap cleanup EXIT

echo "sleeping 30s for ssh to be ready"
sleep 30

gcloud compute scp --zone $ZONE --recurse install_docker_ubuntu.sh "$instance:~"
gcloud compute ssh --zone $ZONE $instance -- 'sh install_docker_ubuntu.sh'
gcloud compute ssh --zone $ZONE $instance -- 'sudo sudo groupadd docker; sudo usermod -aG docker $USER'
gcloud compute ssh --zone $ZONE $instance -- "mkdir -p ~/apps/"
gcloud compute scp --zone $ZONE --recurse apps/ann-benchmarks "$instance:~/apps/"
gcloud compute scp --zone $ZONE --recurse apps/weaviate-no-restart-on-crash/ "$instance:~/apps/"
gcloud compute scp --zone $ZONE --recurse ann_benchmark_compression.sh "$instance:~"
gcloud compute ssh --zone $ZONE $instance -- "WEAVIATE_VERSION=$WEAVIATE_VERSION MACHINE_TYPE=$MACHINE_TYPE CLOUD_PROVIDER=$CLOUD_PROVIDER OS=$OS bash ann_benchmark_compression.sh"
mkdir -p results
gcloud compute scp --zone $ZONE --recurse "$instance:~/results/*.json" results/






