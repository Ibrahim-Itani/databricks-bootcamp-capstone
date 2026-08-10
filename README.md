# Databricks Bootcamp Capstone — Weather MCP Server

This repository implements a Weather Model Context Protocol (MCP) server and companion notebooks for ingesting weather data, building embeddings, and a User Actions API backed by a Lakebase Postgres instance. The code is written in Python and is intended to run inside Databricks (notebooks) and/or as a standalone MCP server component.

Key components

- mcp_server/
  - weather_broker.py — Broker functions that call external weather APIs, transform responses, and interact with Lakebase Postgres for persistence and user actions.
  - weather_mcp_server.py — MCP server wrapper exposing broker functionality as MCP tools (tool wrappers, tracing, and docstrings).
  - lakebase.py — Helper utilities for connecting to Lakebase (Postgres) and common DB helpers.
  - app.yaml — App configuration for deployment (example/service config).
  - requirements.txt — Python dependencies for the server (see file for exact pinning).

- notebooks/
  - ingest_weather_data.py — Notebook/script to ingest raw weather data into the weather_documents table.
  - ingest_weather_embeddings.py.py — Notebook/script to generate embeddings for weather documents and persist into the embeddings table (note: filename contains a duplicated `.py` suffix).
  - user_actions_api.py — Notebook providing the User Actions API (favorites, trips, notes, alerts) that integrates with Lakebase.

- sql/
  - 01_setup_weather_documents_table.sql — Creates the weather_documents table used by the ingestion pipeline.
  - 02_setup_embeddings_table.sql — Creates the embeddings table (pgvector) used for semantic search.
  - 03_setup_user_actions_table.sql — Creates tables for user actions: user_favorite_locations, user_trips, user_notes, user_alerts and supporting indexes/triggers.

- setup_secrets.py — One-time helper script that creates a Databricks secret scope and stores the Lakebase connection URL in Databricks secrets.
- USER_ACTIONS_README.md — Detailed documentation for the User Actions API and integration notes.
- USER_ACTIONS_INTEGRATION.md — Summary of user actions integration and examples.
- LICENSE — Repository license.

Quick start / setup

1. Python environment

- The server and notebooks use Python. Install dependencies for the MCP server using mcp_server/requirements.txt and any additional packages used in the notebooks (see imports in notebooks for full list).

2. Database (Lakebase Postgres)

- Create your Lakebase Postgres database and run the SQL schema files in `sql/`.

  Example:
  psql $LAKEBASE_URL -f sql/01_setup_weather_documents_table.sql
  psql $LAKEBASE_URL -f sql/02_setup_embeddings_table.sql
  psql $LAKEBASE_URL -f sql/03_setup_user_actions_table.sql

- The user actions schema is required for the favorites/trips/notes/alerts features and is documented in USER_ACTIONS_README.md.

3. Databricks secrets (recommended)

- Store your Lakebase connection URL in a Databricks secrets scope named `database` with key `lakebase-url`.

  Use the included helper (run locally with Databricks CLI configured or from a notebook):
  ```bash
  python setup_secrets.py
  ```

  The secret should be a URL of the form: `postgresql://user:pass@host:port/dbname`.

4. Running ingestion and embeddings

- Open and run the notebooks under `notebooks/` (or run the scripts directly) to ingest weather data and build embeddings. Note the filename `ingest_weather_embeddings.py.py` includes an extra `.py` in the repository — you may want to rename it locally to `ingest_weather_embeddings.py`.

5. Running the MCP server

- The MCP tool wrappers are implemented in `mcp_server/weather_mcp_server.py` and depend on the broker logic in `mcp_server/weather_broker.py`.
- The MCP server exposes tools used by Databricks Assistant / MCP clients (tool names and signatures are documented in the code and in USER_ACTIONS_README.md).

6. Secrets and credentials

- The MCP server and notebooks read Lakebase connection details from Databricks secrets (scope `database`, key `lakebase-url`) and may require Databricks SDK credentials when run from non-Databricks environments.

Notes and repository specifics

- User Actions API: implemented in `notebooks/user_actions_api.py` and backed by the SQL in `sql/03_setup_user_actions_table.sql`. See USER_ACTIONS_README.md for examples, schema, and usage patterns.
- Observability: the MCP server uses a `@trace_tool` decorator and stores traces in a DB table for tool observability (see code comments in the broker and server files).
- Filenames: `notebooks/ingest_weather_embeddings.py.py` appears to have a duplicated extension; consider renaming for clarity.

Contributing

- Bug fixes and improvements are welcome. When modifying database schemas or the ingestion pipeline, include SQL migrations and update the README and USER_ACTIONS_README.md as appropriate.

Support / References

- See USER_ACTIONS_README.md and USER_ACTIONS_INTEGRATION.md for detailed API references and integration notes.
- For Databricks-specific setup refer to Databricks documentation for secrets, jobs, and workspace SDK usage.

