import weaviate
import datetime
import time
from loguru import logger
from typing import Optional
import random
import numpy as np
import uuid
import sys

def reset_schema(client: weaviate.Client, class_names):
    client.schema.delete_all()
    for class_name in class_names:
        class_obj = {
            "vectorizer": "none",
            "vectorIndexConfig":{
                "efConstruction": 64,
                "maxConnections": 4,
                "cleanupIntervalSeconds": 10,
            },
            "class": class_name,
            "invertedIndexConfig":{
                "indexTimestamps":False,
            },
            "properties": [
                {
                    "dataType": [ "boolean" ],
                    "name": "should_be_deleted",
                },
                {
                    "dataType": [ "boolean" ],
                    "name": "is_divisible_by_four",
                },
                {
                    "dataType": [ "int" ],
                    "name": "index_id",
                },
                {
                    "dataType": [ "string" ],
                    "name": "stage",
                },
            ]
        }

        client.schema.create_class(class_obj)

def handle_errors(results: Optional[dict]) -> None:
    """
    Handle error message from batch requests logs the message as an info message.
    Parameters
    ----------
    results : Optional[dict]
        The returned results for Batch creation.
    """

    if results is not None:
        for result in results:
            if (
                'result' in result
                and 'errors' in result['result']
                and 'error' in result['result']['errors']
            ):
                for message in result['result']['errors']['error']:
                    logger.error(message['message'])

def load_records(client: weaviate.Client, class_name="Class", start=0, end=100_000, stage="stage_0"):

    client.batch.configure(batch_size=100, callback=handle_errors)
    with client.batch as batch:
        delete_threshold = (end-start)*0.1 + start
        for i in range(start, end):
            if i % 10000 == 0:
                logger.info(f"Class: {class_name} - writing record {i}/{end}")
            data_object={
                "should_be_deleted": i<delete_threshold, # mark 10% of all records for future deletion
                "index_id": i, # same as UUID, this way we can retrieve both using the primary key and the inverted index and make sure the results match
                "stage": stage, # allows for setting filters that match the import stage
                "is_divisible_by_four": i%4 == 0, # an arbitrary field that matches 1/4 of the dataset to allow filtered searches later on
            }
            vector=np.random.rand(32,1)
            batch.add_data_object(
                data_object=data_object,
                vector=vector,
                class_name=class_name,
                uuid=uuid.UUID(int=i),
            )
    logger.info(f"Finished writing {end-start} records")

def delete_records(client: weaviate.Client, class_name):
    client.batch.delete_objects(
        class_name=class_name,
        where={
            'operator': 'Equal',
            'path': ['should_be_deleted'],
            'valueBoolean': True
        },
        output='minimal',
        dry_run=False,
    )

def fatal(msg):
    logger.error(msg)
    sys.exit(1)

def success(msg):
    logger.success(msg)

def validate_dataset(client: weaviate.Client, class_name, expected_count=0):
    # the filter removes 1/4 of elements
    expected_filtered_count = expected_count * 3 / 4

    result = client.query.aggregate(class_name) \
        .with_fields('meta { count }') \
        .with_fields("index_id {count}") \
        .do()

    logger.info("Aggregation without filters:")
    total_count = result['data']['Aggregate'][class_name][0]['meta']['count']
    prop_count = result['data']['Aggregate'][class_name][0]['index_id']['count']

    if total_count != expected_count:
        fatal(f"{class_name}: got {total_count} objects, wanted {expected_count}")
    else:
        success(f"{class_name}: got {total_count} objects, wanted {expected_count}")

    if prop_count != expected_count:
        fatal(f"{class_name}: got {prop_count} props, wanted {expected_count}")
    else:
        success(f"{class_name}: got {prop_count} props, wanted {expected_count}")

    logger.info("Aggregation with filters")
    result = client.query.aggregate(class_name) \
        .with_where({'operator': 'Equal', 'valueBoolean':False, 'path':["is_divisible_by_four"]}) \
        .with_fields('meta { count }') \
        .with_fields("index_id {count}") \
        .do()

    total_count = result['data']['Aggregate'][class_name][0]['meta']['count']
    prop_count = result['data']['Aggregate'][class_name][0]['index_id']['count']

    if total_count != expected_filtered_count:
        fatal(f"{class_name}: got {total_count} objects, wanted {expected_filtered_count}")
    else:
        success(f"{class_name}: got {total_count} objects, wanted {expected_filtered_count}")

    if prop_count != expected_filtered_count:
        fatal(f"{class_name}: got {prop_count} props, wanted {expected_filtered_count}")
    else:
        success(f"{class_name}: got {prop_count} props, wanted {expected_filtered_count}")

def validate_stage(client: weaviate.Client, class_name, start=0, end=100_000, stage="stage_0"):
    start_without_deleted = int((end-start)*0.1 + start)

    logger.info("Retrieve objects using their uuid:")
    for i in range(start_without_deleted, end):
        data_object = client.data_object.get_by_id(str(uuid.UUID(int=i)), class_name=class_name)
        index_id = int(data_object['properties']['index_id'])
        if index_id != i: 
            fatal(f"object {str(uuid.UUID(int=i))} has index_id prop {index_id} instead of {i}")
        if i % 10000 == 0:
            success(f"validated {i}/{end} objects using their uuid")

    logger.info("Retrieve objects using a filter on a unique prop")
    for i in range(start_without_deleted, end):
        where_filter = {
          "path": ["index_id"],
          "operator": "Equal",
          "valueInt": i
        }

        result = (
          client.query
          .get(class_name, "index_id")
          .with_where(where_filter)
          .do()
        )
        index_id = int(result['data']['Get'][class_name][0]['index_id'])
        if index_id != i: 
            fatal(f"object has index_id prop {index_id} instead of {i}")
        if i % 10000 == 0:
            success(f"validated {i}/{end} objects using a filter")

    logger.info("Perform vector search without filter")
    logger.info("Note: This test currently does not validate the quality (e.g. recall) of the results, only that it works")
    for i in range(start_without_deleted, end):
        near_object = {
                'id': str(uuid.UUID(int=i)),
        }
        limit = 20

        result = (
          client.query
          .get(class_name, "index_id")
          .with_near_object(near_object)
          .with_limit(limit)
          .do()
        )
        result_len = len(result['data']['Get'][class_name])
        if result_len != limit: 
            fatal(f"vector search has result len {result_len} wanted {limit}")
        if i % 10000 == 0:
            success(f"validated {i}/{end} vector searches")

    logger.info("Perform vector search with filter")
    logger.info("Note: This test currently does not validate the quality (e.g. recall) of the results, only that it works")
    for i in range(start_without_deleted, end):
        near_object = {
                'id': str(uuid.UUID(int=i)),
        }
        where = {
            'operator':'Equal',
            'valueString': stage,
            'path': ['stage'],

                }
        limit = 20

        result = (
          client.query
          .get(class_name, "index_id")
          .with_near_object(near_object)
          .with_where(where)
          .with_limit(limit)
          .do()
        )
        result_len = len(result['data']['Get'][class_name])
        if result_len != limit: 
            fatal(f"vector search has result len {result_len} wanted {limit}")
        if i % 10000 == 0:
            success(f"validated {i}/{end} vector searches")


client = weaviate.Client("http://localhost:8080")

class_names=['Class_A', 'Class_B']
objects_per_stage = 50_000
start_stage_1 = 0
end_stage_1 = objects_per_stage
expected_count_stage_1 = 0.9 * end_stage_1 # because of 10% deletions
start_stage_2 = end_stage_1
end_stage_2 = start_stage_2 + objects_per_stage
expected_count_stage_2 = 0.9 * end_stage_2 # because of 10% deletions

logger.info(f"Step 0, reset everything, import schema")
reset_schema(client, class_names)

logger.info(f"Step 1, import first half of objects across {len(class_names)} classes")
for class_name in class_names:
    load_records(client, class_name, start=start_stage_1, end=end_stage_1, stage="stage_1")

logger.info("Step 2, delete 10% of objects to make sure deletes are covered")
for class_name in class_names:
    delete_records(client, class_name)

logger.info("Step 3, run control test on original instance validating all assumptions at stage 1")
for class_name in class_names:
    logger.info(f"{class_name}:")
    validate_dataset(client, class_name, expected_count=expected_count_stage_1)
    validate_stage(client, class_name, start=start_stage_1, end=end_stage_1, stage="stage_1")
logger.info("Step 4, create backup of current instance including all classes")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

logger.info(f"Step 5, import second half of objects across {len(class_names)} classes")
for class_name in class_names:
    load_records(client, class_name, start=start_stage_2, end=end_stage_2, stage="stage_2")

logger.info("Step 6, delete 10% of objects to make sure deletes are covered")
for class_name in class_names:
    delete_records(client, class_name)

logger.info("Step 7, validate both stages on control instance")

for class_name in class_names:
    logger.info(f"{class_name} - Overall:")
    validate_dataset(client, class_name, expected_count=expected_count_stage_2)

for class_name in class_names:
    logger.info(f"{class_name} - Stage 1:")
    validate_stage(client, class_name, start=start_stage_1, end=end_stage_1, stage="stage_1")

for class_name in class_names:
    logger.info(f"{class_name} - Stage 2:")
    validate_stage(client, class_name, start=start_stage_2, end=end_stage_2, stage="stage_2")

logger.info("Step 8, delete all classes")
client.schema.delete_all()

logger.info("Step 9, restore backup at half-way mark")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

logger.info("Step 10, run test and make sure results are same as on original instance at stage 1")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

logger.info("Step 11, import second half of objects")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

logger.info("Step 12, delete 10% of objects of new import")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

logger.info("Step 13, run test and make sure results are same as on original instance at stage 2")
logger.warning("SKIPPED - WAITING FOR BACKUP IMPLEMENTATION TO COMPLETE")

