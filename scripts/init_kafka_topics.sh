#!/bin/bash
kafka-topics --create --if-not-exists --topic user_events --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
kafka-topics --create --if-not-exists --topic processed_features --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
echo "Kafka topics created."
