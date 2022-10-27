#!/bin/bash

set -e

function wait_weaviate_cluster() {
  echo "Wait for Weaviate to be ready"
  local node1_ready=false
  local node2_ready=false
  for _ in {1..120}; do
    if curl -sf -o /dev/null localhost:8080; then
      echo "Weaviate node1 is ready"
      node1_ready=true
    fi

    if curl -sf -o /dev/null localhost:8081; then
      echo "Weaviate node2 is ready"
      node2_ready=true
    fi

    if $node1_ready && $node2_ready; then
      break
    fi

    echo "Weaviate cluster is not ready, trying again in 1s"
    sleep 1
  done
}

echo "Building app container"
( cd apps/backup_and_restore_version_compatibility/ && docker build -t backup_and_restore_version_compatibility . )

echo "Generating version pairs"
cd apps/backup_and_restore_version_compatibility/ && docker build -f Dockerfile_gen_version_pairs \
    -t generate_version_pairs --build-arg weaviate_version=${WEAVIATE_VERSION} .
cd -

pair_string=$(docker run --rm generate_version_pairs)
if [[ $pair_string =~ 'failed' ]]; then
  echo "ERROR: ${pair_string}"
  exit 1
fi

version_pairs=($pair_string)

# run backup/restore ops for each version pairing
for pair in "${!version_pairs[@]}"; do 
  backup_version=$(echo "${version_pairs[$pair]}" | cut -f1 -d+)
  restore_version=$(echo "${version_pairs[$pair]}" | cut -f2 -d+)

  export WEAVIATE_NODE_1_VERSION=$backup_version
  export WEAVIATE_NODE_2_VERSION=$restore_version

  echo "Starting Weaviate cluster..."
  docker-compose -f apps/weaviate/docker-compose-backup.yml up -d weaviate-node-1 weaviate-backup-node backup-gcs

  wait_weaviate_cluster

  echo "Run backup (v${backup_version}) and restore (v${restore_version}) version compatibility operations"
  docker run --rm --network host -it backup_and_restore_version_compatibility python3 backup_and_restore_version_compatibility.py

  echo "Cleaning up containers for next test..."
  docker-compose -f apps/weaviate/docker-compose-backup.yml down --remove-orphans
done

echo "Passed!"
