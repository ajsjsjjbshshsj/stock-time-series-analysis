# Kafka Topic Design

This local development environment runs a single Kafka 4.0.0 KRaft broker. Topic auto-creation is disabled so the JSON topic contract is explicit and reproducible.

| Topic | Purpose | Partitions | Replication factor |
| --- | --- | ---: | ---: |
| `stock.ods.daily.v1` | Daily stock market ODS JSON events for downstream processing. | 3 | 1 |
| `stock.ods.basic.v1` | Stock master/basic-information ODS JSON events. | 1 | 1 |
| `stock.dead-letter.v1` | JSON events that cannot be processed successfully, retained for diagnosis and recovery. | 1 | 1 |

## Start and inspect

Start the environment from the repository root:

```powershell
docker compose -f infrastructure/docker-compose.yml up -d
```

Check the broker, initializer, and UI status:

```powershell
docker compose -f infrastructure/docker-compose.yml ps
```

Inspect the created topics and their partition counts:

```powershell
docker exec stock-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe
```

Kafka UI is available at [http://localhost:8081](http://localhost:8081).

Inspect service logs when startup or topic initialization needs diagnosis:

```powershell
docker compose -f infrastructure/docker-compose.yml logs kafka
docker compose -f infrastructure/docker-compose.yml logs topic-init
docker compose -f infrastructure/docker-compose.yml logs kafka-ui
```

## Recoverable shutdown

Stop the environment without deleting Docker volumes or other persistent Docker-managed state:

```powershell
docker compose -f infrastructure/docker-compose.yml down
```

Do not add `-v` for normal local shutdown and restart workflows.
