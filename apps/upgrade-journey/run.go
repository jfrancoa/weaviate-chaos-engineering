package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

// TODO: should be automated by pulling them from GH tags
var versions = []string{
	"1.16.0",
	"1.16.1",
	"1.16.2",
	"1.16.3",
	"1.16.4",
	"1.16.5",
	"1.16.6",
	"1.16.7",
	"1.16.8",
	"1.16.9",
	"1.17.0",
	"1.17.1",
	"1.17.2",
}

var objectsCreated = 0

func main() {
	cfg := weaviate.Config{
		Host:   "localhost:8080",
		Scheme: "http",
	}
	client := weaviate.New(cfg)

	err := do(client)
	if err != nil {
		log.Fatal(err)
	}
}

func do(client *weaviate.Client) error {
	rand.Seed(time.Now().UnixNano())
	ctx := context.Background()

	c := newCluster(3)

	if err := c.startNetwork(ctx); err != nil {
		return err
	}

	for i, version := range versions {
		if err := startOrUpgrade(ctx, c, i, version); err != nil {
			return err
		}

		if i == 0 {
			if err := createSchema(ctx, client); err != nil {
				return err
			}
		}

		if err := importForVersion(ctx, client, version); err != nil {
			return err
		}

		if err := verify(ctx, client, i); err != nil {
			return err
		}
	}

	return nil
}

func verify(ctx context.Context, client *weaviate.Client, i int) error {
	if err := findEachImportedObject(ctx, client, i); err != nil {
		return err
	}

	if err := aggregateObjects(ctx, client, i); err != nil {
		return err
	}

	return nil
}

func aggregateObjects(ctx context.Context, client *weaviate.Client,
	count int,
) error {
	result, err := client.GraphQL().Aggregate().
		WithClassName("Collection").
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	if err != nil {
		return err
	}

	if len(result.Errors) > 0 {
		return fmt.Errorf("%v", result.Errors)
	}

	actualCount := result.Data["Aggregate"].(map[string]interface{})["Collection"].([]interface{})[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
	if int(actualCount) != objectsCreated {
		return fmt.Errorf("aggregation: wanted %d, got %d", objectsCreated, int(actualCount))
	}

	return nil
}

func findEachImportedObject(ctx context.Context, client *weaviate.Client,
	posOfMaxVersion int,
) error {
	for i := 0; i <= posOfMaxVersion; i++ {
		version := versions[i]

		fields := []graphql.Field{
			{Name: "_additional { id }"},
			{Name: "version"},
			{Name: "object_count"},
		}
		where := filters.Where().
			WithPath([]string{"version"}).
			WithOperator(filters.Equal).
			WithValueString(version)

		result, err := client.GraphQL().Get().
			WithClassName("Collection").
			WithFields(fields...).
			WithWhere(where).
			Do(ctx)
		if err != nil {
			return err
		}
		if len(result.Errors) > 0 {
			return fmt.Errorf("%v", result.Errors)
		}

		actualVersion := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})[0].(map[string]interface{})["version"].(string)
		if version != actualVersion {
			return fmt.Errorf("wanted %s got %s", version, actualVersion)
		}

	}

	return nil
}

func createSchema(ctx context.Context, client *weaviate.Client) error {
	classObj := &models.Class{
		Class: "Collection",
		Properties: []*models.Property{
			{
				DataType: []string{"string"},
				Name:     "version",
			},
			{
				DataType: []string{"int"},
				Name:     "object_count",
			},
		},
	}

	err := client.Schema().ClassCreator().WithClass(classObj).Do(context.Background())
	if err != nil {
		return err
	}

	return nil
}

func importForVersion(ctx context.Context, client *weaviate.Client,
	version string,
) error {
	props := map[string]interface{}{
		"version":      version,
		"object_count": objectsCreated,
	}

	objectsCreated++
	_, err := client.Data().Creator().
		WithClassName("Collection").
		WithProperties(props).
		Do(context.Background())
	return err
}

func startOrUpgrade(ctx context.Context, c *cluster, i int, version string) error {
	if i == 0 {
		return c.startAllNodes(ctx, version)
	}

	return c.rollingUpdate(ctx, versions[i%len(versions)])
}
