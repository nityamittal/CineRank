# CineRank — Claude Code Build Commands

Copy-paste these commands one at a time into Claude Code.
Type `/clear` between each phase to keep context clean.

**Note:** Docker is not available on this machine. All commands are for generating code and files only. Nothing will be executed or verified at runtime. The project will be fully buildable when Docker becomes available or when deployed to a machine with Docker support.

---

## Start the session

```bash
cd cinerank
claude
```

---

## Phase 1 — Infrastructure

```
Build Phase 1. Create the docker-compose.yml with Kafka, Zookeeper, Redis, and PostgreSQL. Also create the .env and .env.example files, db/init.sql with the full schema, and scripts/init_kafka_topics.sh. Follow the CLAUDE.md instructions exactly. Do NOT run docker-compose or any containers — just create the files.
```

```
Commit with message "Phase 1: infrastructure — Docker Compose, Kafka, Redis, PostgreSQL"
```

```
/clear
```

---

## Phase 2 — Dataset & Seeding

```
Build Phase 2. The MovieLens 32M dataset already exists locally in the folder ml-32m/ with files: ratings.csv (32M ratings), movies.csv (87,585 movies), tags.csv (2M tags), and links.csv. Do NOT download anything. Create scripts/seed_postgres.py to load movies.csv and ratings.csv from ml-32m/ into PostgreSQL, and data/sample_events.json with 10 test events. All file paths in the project should reference ml-32m/ not ml-25m/. Follow the data/CLAUDE.md instructions. Do NOT run the seeding script — just create the files.
```

```
Commit with message "Phase 2: dataset download and PostgreSQL seeding scripts"
```

```
/clear
```

---

## Phase 3 — Kafka Producer

```
Build Phase 3. Create the kafka_producer/ module with produce_events.py, requirements.txt, and Dockerfile. It should replay ratings.csv into Kafka sorted by timestamp with --speed and --limit flags. Follow kafka_producer/CLAUDE.md instructions. Do NOT run the producer or connect to Kafka — just create the files.
```

```
Commit with message "Phase 3: Kafka producer — event replay from MovieLens"
```

```
/clear
```

---

## Phase 4 — Stream Processor

```
Build Phase 4. Create the stream_processor/ module with app.py, requirements.txt, and Dockerfile. It should consume from Kafka, compute per-user features (recent movies, avg rating, genre counts, event count), and store them in Redis using pipelines. Follow stream_processor/CLAUDE.md instructions. Do NOT run the processor or connect to any services — just create the files.
```

```
Commit with message "Phase 4: stream processor — real-time feature extraction to Redis"
```

```
/clear
```

---

## Phase 5 — Recommendation Engine

```
Build Phase 5. Create the recommendation_engine/ module with train_model.py, model.py, utils.py, requirements.txt, and Dockerfile. Use TruncatedSVD with 50 components on the user-item matrix. Save model artifacts to models/ directory. Include cold-start fallback logic for unknown users. Precompute top-N recs for the top 1000 users and cache in Redis. Follow recommendation_engine/CLAUDE.md instructions. Do NOT run the training script or connect to any services — just create the files.
```

```
Commit with message "Phase 5: recommendation engine — SVD training and inference"
```

```
/clear
```

---

## Phase 6 — FastAPI Serving Layer

```
Build Phase 6. Create the api/ module with main.py, schemas.py, requirements.txt, and Dockerfile. Endpoints: GET /health, GET /recommendations, GET /similar, GET /popular, POST /events, GET /user/{user_id}/profile. Load the model on startup, read features from Redis, produce events to Kafka. Add timing middleware. Follow api/CLAUDE.md instructions. Do NOT start the server or connect to any services — just create the files.
```

```
Commit with message "Phase 6: FastAPI serving layer with all endpoints"
```

```
/clear
```

---

## Phase 7 — Full Docker Compose Integration

```
Build Phase 7. Update docker-compose.yml to add the application services: kafka-init (one-shot topic creation), producer, stream processor, and api. Add volume mounts for the model files and dataset. Make sure all depends_on and healthchecks are correct so services start in order. Do NOT run docker-compose — just update the file.
```

```
Commit with message "Phase 7: full Docker Compose stack with all services"
```

```
/clear
```

---

## Phase 8 — Tests

```
Build Phase 8. Create tests/ directory with test_api.py, test_producer.py, test_processor.py, and test_model.py. Use pytest and httpx. Mock Redis and Kafka completely so tests can run without any running services. Add a requirements-test.txt. Follow tests/CLAUDE.md instructions. Do NOT run pytest — just create the test files.
```

```
Commit with message "Phase 8: test suite for all components"
```

```
/clear
```

---

## Phase 9 — README

```
Build Phase 9. Write a comprehensive README.md with the project title "CineRank — Real-Time Personalized Movie Recommendation System using Kafka, Redis, and FastAPI". Include an ASCII architecture diagram, quick start (5 commands), detailed setup, API reference with curl examples, system design section covering scalability and fault tolerance, tech stack table, possible extensions, and a resume-ready bullet point.
```

```
Commit with message "Phase 9: README with architecture diagram and documentation"
```

---

## Phase 10 — Final Review

```
Review the entire project structure. Make sure all imports are correct across modules, all file paths referenced in docker-compose.yml exist, all requirements.txt files have the right dependencies, and there are no missing files or broken references. List any issues you find and fix them. Do NOT run anything — just review the code and fix problems.
```

```
Commit with message "Phase 10: final review — fix imports, paths, and dependencies"
```

---

## Alternative: Build Everything in One Go

If you want to skip the phase-by-phase approach:

```
Read the CLAUDE.md and build the entire CineRank project from Phase 1 through Phase 9. The MovieLens 32M dataset already exists in ml-32m/ — do NOT create a download script. Create all files, Dockerfiles, and the README. Do NOT run any containers, servers, scripts, or tests — only create files. After all phases, do a final review to make sure all imports, paths, and dependencies are correct across the whole project. Commit after each phase with a descriptive message.
```

---

## Useful Commands

```
/clear
```

```
/compact
```

```
/help
```

```
/doctor
```

---

## When You're Ready to Run (Once Docker Is Available)

When you get Docker working later, here's the sequence to bring everything up:

```
The MovieLens 32M dataset is already in ml-32m/. Run docker-compose up -d to start infrastructure, then python scripts/seed_postgres.py to load data, then python recommendation_engine/train_model.py to train the model, then docker-compose up --build to start all services. Finally run pytest to verify tests pass.
```

---

## If Something Breaks During Code Generation

```
Review all Python files in the project for syntax errors or import issues. Fix anything you find without running the code.
```

```
Check that every module referenced in docker-compose.yml has a matching Dockerfile and requirements.txt. Fix any missing files.
```

```
Verify all Pydantic schemas in api/schemas.py match the actual response structures returned by the API endpoints in api/main.py. Fix any mismatches.
```

```
Make sure the recommendation_engine/model.py correctly imports from utils.py and that the RecommendationModel class matches what api/main.py expects. Fix any interface mismatches.
```
