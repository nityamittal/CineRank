# CLAUDE.md — CineRank: Infrastructure Setup

This file covers Phase 1: getting Docker Compose, Kafka, Redis, and PostgreSQL running.

## Goal

Run `docker-compose up -d` and have Kafka, Redis, and PostgreSQL healthy and ready to accept connections.

## Tasks

### 1. Create `docker-compose.yml`

Services:

```yaml
version: "3.8"

services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    ports:
      - "2181:2181"
    healthcheck:
      test: ["CMD", "echo", "ruok", "|", "nc", "localhost", "2181"]
      interval: 10s
      timeout: 5s
      retries: 5

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      zookeeper:
        condition: service_healthy
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
    healthcheck:
      test: ["CMD", "kafka-broker-api-versions", "--bootstrap-server", "localhost:9092"]
      interval: 10s
      timeout: 10s
      retries: 10

  kafka-init:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      kafka:
        condition: service_healthy
    entrypoint: ["/bin/bash", "-c"]
    command: |
      "
      kafka-topics --create --if-not-exists --topic user_events --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
      kafka-topics --create --if-not-exists --topic processed_features --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
      echo 'Topics created successfully.'
      "
    restart: "no"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    volumes:
      - redis-data:/data

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: recdb
      POSTGRES_USER: recuser
      POSTGRES_PASSWORD: recpass
    volumes:
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U recuser -d recdb"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis-data:
  postgres-data:
```

### 2. Create `db/init.sql`

Full schema with indexes. See main CLAUDE.md Phase 1 for the SQL.

### 3. Create `.env` and `.env.example`

Both files identical initially. Keep passwords out of docker-compose.yml by referencing `${POSTGRES_PASSWORD}` etc.

### 4. Verification Checklist

```bash
# Start everything
docker-compose up -d

# Check all containers are healthy
docker-compose ps

# Test Kafka
docker exec -it $(docker-compose ps -q kafka) kafka-topics --list --bootstrap-server localhost:9092
# Should show: user_events, processed_features

# Test Redis
docker exec -it $(docker-compose ps -q redis) redis-cli ping
# Should return: PONG

# Test PostgreSQL
docker exec -it $(docker-compose ps -q postgres) psql -U recuser -d recdb -c "SELECT tablename FROM pg_tables WHERE schemaname='public';"
# Should show: users, movies, interactions
```

## Important Notes

- Use `condition: service_healthy` in depends_on — not just `depends_on` — so services start in order
- kafka-init is a one-shot container (restart: "no") that creates topics then exits
- All data volumes are named so they persist across `docker-compose down` / `up`
- For a clean reset: `docker-compose down -v` removes volumes
