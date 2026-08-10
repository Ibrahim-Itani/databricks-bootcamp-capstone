# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# DBTITLE 1,Weather Data Ingestion Pipeline
# MAGIC %md
# MAGIC # Weather Data Ingestion Pipeline
# MAGIC
# MAGIC Fetches weather alerts and forecasts from the National Weather Service (NWS) API
# MAGIC for configured locations and syncs them to the `weather_documents` Lakebase table.
# MAGIC
# MAGIC ## Prerequisites
# MAGIC
# MAGIC 1. Run `sql/01_setup_weather_table.sql` to create the `weather_documents` table
# MAGIC 2. Configure locations to monitor via the widgets below
# MAGIC 3. Ensure Lakebase connection is configured in secrets
# MAGIC
# MAGIC ## What This Notebook Does
# MAGIC
# MAGIC 1. Fetches active weather alerts and forecasts for specified locations
# MAGIC 2. Normalizes the data into a consistent document format
# MAGIC 3. Upserts documents into Lakebase Postgres using psycopg2
# MAGIC 4. ON CONFLICT DO UPDATE ensures data stays fresh on re-runs

# COMMAND ----------

# DBTITLE 1,Install packages
# MAGIC %pip uninstall -y psycopg2 psycopg2-binary
# MAGIC %pip install -q 'databricks-sdk>=0.118.0' sentence-transformers trafilatura requests pandas

# COMMAND ----------

# DBTITLE 1,Restart kernel
dbutils.library.restartPython()

# COMMAND ----------

# DBTITLE 1,Configuration
# MAGIC %md
# MAGIC ## Configuration
# MAGIC
# MAGIC Widgets let you override locations and limits without editing the notebook.

# COMMAND ----------

# DBTITLE 1,Setup config widgets
dbutils.widgets.text("locations", "Chicago, IL|New York, NY|Los Angeles, CA", "Locations (pipe-separated)")
dbutils.widgets.text("weather_table_name", "weather_documents", "Destination table")
dbutils.widgets.text("fetch_limit", "10", "Max documents per location")
dbutils.widgets.text("chunk_size", "800", "Article content chunk size (chars)")
dbutils.widgets.text("chunk_overlap", "100", "Article content chunk overlap (chars)")
dbutils.widgets.text("embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model")
dbutils.widgets.text("embeddings_table_name", "weather_documents_embeddings", "Destination table (vectors)")

LOCATIONS = [loc.strip() for loc in dbutils.widgets.get("locations").split("|") if loc.strip()]
WEATHER_TABLE_NAME = dbutils.widgets.get("weather_table_name")
FETCH_LIMIT = int(dbutils.widgets.get("fetch_limit"))
EMBEDDINGS_TABLE_NAME = dbutils.widgets.get("embeddings_table_name")
EMBEDDING_MODEL_NAME = dbutils.widgets.get("embedding_model")
CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))

# Different sentence-transformers models emit different vector sizes, and the# pgvector column type (VECTOR(N)) must match exactly. Rather than hardcoding# one dimension, switch on the model name so swapping EMBEDDING_MODEL_NAME via# the widget above automatically resizes the destination table's vector column.

match EMBEDDING_MODEL_NAME:    
    case "sentence-transformers/all-MiniLM-L6-v2":        
        EMBEDDING_DIM = 384    
    case "sentence-transformers/all-MiniLM-L12-v2":        
        EMBEDDING_DIM = 384    
    case "sentence-transformers/all-mpnet-base-v2":        
        EMBEDDING_DIM = 768    
    case "sentence-transformers/paraphrase-multilingual-mpnet-base-v2":         
        EMBEDDING_DIM = 768    
    case "BAAI/bge-small-en-v1.5":       
        EMBEDDING_DIM = 384    
    case "BAAI/bge-base-en-v1.5":        
        EMBEDDING_DIM = 768    
    case "BAAI/bge-large-en-v1.5":        
        EMBEDDING_DIM = 1024    
    case "text-embedding-3-small":        
        EMBEDDING_DIM = 1536    
    case "text-embedding-3-large":        
        EMBEDDING_DIM = 3072    
    case _:        
        raise ValueError(            
            f"Unknown embedding model {EMBEDDING_MODEL_NAME!r} - add its      output "            
            "dimension to the match/case block above before running this      otebook."        
        )

print(f"Using model {EMBEDDING_MODEL_NAME!r} -> {EMBEDDING_DIM}-dim vectors")

print(f"Will fetch weather for {len(LOCATIONS)} locations:")
for loc in LOCATIONS:
    print(f"  - {loc}")
print(f"\nLimit: {FETCH_LIMIT} documents per location")
print(f"Destination table: {WEATHER_TABLE_NAME}")

# COMMAND ----------

# DBTITLE 1,Parse Lakebase connection
import base64
from urllib.parse import urlparse
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

def get_lakebase_url() -> str:
    secret = w.secrets.get_secret(scope="database", key="lakebase-url")
    return base64.b64decode(secret.value).decode("utf-8")

lakebase_url = get_lakebase_url()
parsed = urlparse(lakebase_url)

db_host = parsed.hostname
db_port = parsed.port or 5432
db_name = parsed.path.lstrip('/')
db_user = parsed.username
db_password = parsed.password

print(f"Connection details:")
print(f"  Host: {db_host}:{db_port}")
print(f"  Database: {db_name}")
print(f"  User: {db_user}")

# COMMAND ----------

# DBTITLE 1,Test Psycopg2 connection
import psycopg2

print(f"Testing connection to {db_host}:{db_port}/{db_name}")
print(f"Using OAuth token authentication as user: {db_user}\n")

# Test psycopg3 connection with OAuth token
try:
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode='require',
        connect_timeout=10
    )
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_TABLE_NAME}")
    count = cursor.fetchone()[0]
    print(f"✅ Connection successful! Found {count} rows in {WEATHER_TABLE_NAME}")
    
    cursor.execute(f"SELECT * FROM {WEATHER_TABLE_NAME} LIMIT 5")
    rows = cursor.fetchall()
    colnames = [desc[0] for desc in cursor.description]
    print(f"\nColumns: {colnames}")
    for row in rows:
        print(row)
    
    cursor.close()
    conn.close()
    print("\n✅ psycopg3 with OAuth authentication working correctly!")
except Exception as e:
    import traceback
    print(f"❌ Connection failed: {e}")
    print(f"\nFull traceback:")
    traceback.print_exc()

# COMMAND ----------

# DBTITLE 1,Fetch weather data
# MAGIC %md
# MAGIC ## Fetch Weather Data
# MAGIC
# MAGIC Uses `weather_client.py` to fetch alerts and forecasts from NWS API.
# MAGIC Returns normalized documents ready for database insertion.

# COMMAND ----------

import base64
import hashlib
import os
from datetime import datetime
from typing import Any
import requests

_BASE_URL = os.environ.get("MASSIVE_API_BASE_URL", "https://api.weather.gov")

_DEFAULT_TIMEOUT = 30

class WeatherClient:
  """Thin wrapper around the NWS Weather API + retry-friendly session.
  GET /alerts/active?area={state} → active weather alerts, each with a free-text description and instruction field (e.g., "A Flash Flood Warning means...").
  GET /gridpoints/{office}/{x},{y}/forecast → multi-day forecast with a narrative detailedForecast string per period (e.g., "Sunny, with a high near 78. Northwest wind around 6 mph.").
  GET /gridpoints/{office}/{x},{y}/forecast/hourly → hourly narrative forecasts."""
  
  def __init__(self):
    """Initialize the Weather client with a session."""
    self.session = requests.Session()
    self.session.headers.update({
      "User-Agent": "(Databricks Weather App, contact@example.com)",  # NWS requires a User-Agent
      "Accept": "application/geo+json"
    })
    self.base_url = _BASE_URL
  
  def get_active_alerts(self, state: str) -> dict[str, Any]:
    """Fetch active weather alerts for a given state.
    
    Args:
      state: Two-letter state code (e.g., 'CA', 'TX')
    
    Returns:
      Dict containing alert features with rich narrative text in
      properties.description and properties.instruction fields.
    """
    url = f"{self.base_url}/alerts/active"
    params = {"area": state.upper()}
    
    response = self.session.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()
  
  def get_forecast(self, office: str, grid_x: int, grid_y: int) -> dict[str, Any]:
    """Fetch multi-day forecast with narrative text.
    
    Args:
      office: NWS Weather Forecast Office identifier (e.g., 'LOX' for Los Angeles)
      grid_x: Grid X coordinate
      grid_y: Grid Y coordinate
    
    Returns:
      Dict with forecast periods, each containing a detailedForecast
      narrative string (e.g., "Sunny, with a high near 78...")
    """
    url = f"{self.base_url}/gridpoints/{office}/{grid_x},{grid_y}/forecast"
    
    response = self.session.get(url, timeout=_DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()
  
  def get_hourly_forecast(self, office: str, grid_x: int, grid_y: int) -> dict[str, Any]:
    """Fetch hourly forecast with narrative text.
    
    Args:
      office: NWS Weather Forecast Office identifier
      grid_x: Grid X coordinate
      grid_y: Grid Y coordinate
    
    Returns:
      Dict with hourly forecast periods with narrative descriptions
    """
    url = f"{self.base_url}/gridpoints/{office}/{grid_x},{grid_y}/forecast/hourly"
    
    response = self.session.get(url, timeout=_DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()
  
  def get_grid_point(self, latitude: float, longitude: float) -> dict[str, Any]:
    """Convert lat/lon to NWS grid coordinates.
    
    Args:
      latitude: Latitude coordinate
      longitude: Longitude coordinate
    
    Returns:
      Dict containing the gridId, gridX, and gridY needed for forecast calls
    """
    url = f"{self.base_url}/points/{latitude},{longitude}"
    
    response = self.session.get(url, timeout=_DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()


def geocode_location(location_str: str) -> tuple[float, float] | None:
  """Simple geocoding for major US cities. In production, use a real geocoding API.
  
  Args:
    location_str: Location string like "Chicago, IL" or "lat,lon" format
  
  Returns:
    Tuple of (latitude, longitude) or None if not found
  """
  # Try to parse as lat,lon first
  try:
    parts = location_str.strip().split(',')
    if len(parts) == 2:
      lat = float(parts[0].strip())
      lon = float(parts[1].strip())
      # Basic validation for lat/lon ranges
      if -90 <= lat <= 90 and -180 <= lon <= 180:
        return (lat, lon)
  except ValueError:
    pass
  
  # Simple city lookup (expand as needed)
  city_coords = {
    "chicago, il": (41.8781, -87.6298),
    "austin, tx": (30.2672, -97.7431),
    "new york, ny": (40.7128, -74.0060),
    "los angeles, ca": (34.0522, -118.2437),
    "san francisco, ca": (37.7749, -122.4194),
    "seattle, wa": (47.6062, -122.3321),
    "miami, fl": (25.7617, -80.1918),
    "denver, co": (39.7392, -104.9903),
    "boston, ma": (42.3601, -71.0589),
    "atlanta, ga": (33.7490, -84.3880),
  }
  
  return city_coords.get(location_str.lower())


def normalize_alert(alert_feature: dict, location: str) -> dict[str, Any]:
  """Normalize a weather alert feature into our database format.
  
  Args:
    alert_feature: GeoJSON feature from NWS alerts API
    location: Original location string
  
  Returns:
    Dict ready for database insertion
  """
  props = alert_feature.get("properties", {})
  
  # Create a unique ID from alert ID or hash of content
  alert_id = props.get("id") or hashlib.sha256(
    f"{location}:{props.get('event')}:{props.get('onset')}".encode()
  ).hexdigest()[:16]
  
  # Combine description and instruction for rich narrative text
  description = props.get("description", "")
  instruction = props.get("instruction", "")
  narrative = f"{description}\n\n{instruction}" if instruction else description
  
  return {
    "id": alert_id,
    "location": location,
    "source_type": "alert",
    "headline": props.get("event") or props.get("headline") or "Weather Alert",
    "narrative_text": narrative,
    "issued_at": props.get("onset") or props.get("sent"),
    "payload": alert_feature,
  }


def normalize_forecast(forecast_period: dict, location: str, office: str, grid_x: int, grid_y: int) -> dict[str, Any]:
  """Normalize a forecast period into our database format.
  
  Args:
    forecast_period: Single period from NWS forecast API
    location: Original location string
    office: NWS office identifier
    grid_x: Grid X coordinate
    grid_y: Grid Y coordinate
  
  Returns:
    Dict ready for database insertion
  """
  # Create unique ID from location and period info
  period_id = hashlib.sha256(
    f"{location}:{office}:{grid_x},{grid_y}:{forecast_period.get('number')}:{forecast_period.get('startTime')}".encode()
  ).hexdigest()[:16]
  
  return {
    "id": period_id,
    "location": location,
    "source_type": "forecast",
    "headline": forecast_period.get("name", "Forecast"),
    "narrative_text": forecast_period.get("detailedForecast", ""),
    "issued_at": forecast_period.get("startTime"),
    "payload": forecast_period,
  }


def sync_weather_data(locations: list[str], limit: int = 50) -> int:
  """Fetch weather alerts and forecasts for given locations and sync to database.
  
  Args:
    locations: List of location strings ("City, ST" or "lat,lon")
    limit: Maximum number of documents to sync per location
  
  Returns:
    Total count of documents synced
  """
  client = WeatherClient()
  all_documents = []
  
  for location in locations:
    # Geocode the location
    coords = geocode_location(location)
    if not coords:
      print(f"Warning: Could not geocode location '{location}', skipping")
      continue
    
    lat, lon = coords
    
    try:
      # Get grid point info
      grid_data = client.get_grid_point(lat, lon)
      grid_props = grid_data.get("properties", {})
      office = grid_props.get("gridId")
      grid_x = grid_props.get("gridX")
      grid_y = grid_props.get("gridY")
      
      if not all([office, grid_x, grid_y]):
        print(f"Warning: Could not get grid data for {location}, skipping")
        continue
      
      # Fetch active alerts for the state (extract from location)
      try:
        state = location.split(",")[-1].strip() if "," in location else None
        if state and len(state) == 2:
          alerts_data = client.get_active_alerts(state)
          for feature in alerts_data.get("features", [])[:limit]:
            all_documents.append(normalize_alert(feature, location))
      except Exception as e:
        print(f"Warning: Could not fetch alerts for {location}: {e}")
      
      # Fetch forecast
      try:
        forecast_data = client.get_forecast(office, grid_x, grid_y)
        for period in forecast_data.get("properties", {}).get("periods", [])[:limit]:
          all_documents.append(normalize_forecast(period, location, office, grid_x, grid_y))
      except Exception as e:
        print(f"Warning: Could not fetch forecast for {location}: {e}")
      
    except Exception as e:
      print(f"Error processing location '{location}': {e}")
      continue
  
  # Upsert into database
  if not all_documents:
    return 0
  
  # Lazy import to avoid module initialization issues
  from lakebase import get_connection
  
  with get_connection() as conn:
    with conn.cursor() as cur:
      # Use INSERT ... ON CONFLICT DO UPDATE for upsert
      upsert_sql = """
        INSERT INTO weather_documents (
          id, location, source_type, headline, narrative_text, issued_at, payload, synced_at
        ) VALUES (
          %(id)s, %(location)s, %(source_type)s, %(headline)s, %(narrative_text)s, 
          %(issued_at)s, %(payload)s, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
          location = EXCLUDED.location,
          source_type = EXCLUDED.source_type,
          headline = EXCLUDED.headline,
          narrative_text = EXCLUDED.narrative_text,
          issued_at = EXCLUDED.issued_at,
          payload = EXCLUDED.payload,
          synced_at = NOW()
      """
      
      for doc in all_documents:
        cur.execute(upsert_sql, doc)
      
      conn.commit()
      return len(all_documents)

# COMMAND ----------

# DBTITLE 1,Fetch weather using weather_client
print(f"Fetching weather for {len(LOCATIONS)} locations...\n")

client = WeatherClient()
weather_docs = []

for location in LOCATIONS:
    # Geocode the location
    coords = geocode_location(location)
    if not coords:
        print(f"Warning: Could not geocode location '{location}', skipping")
        continue
    
    lat, lon = coords
    
    try:
        # Get grid point info
        grid_data = client.get_grid_point(lat, lon)
        grid_props = grid_data.get("properties", {})
        office = grid_props.get("gridId")
        grid_x = grid_props.get("gridX")
        grid_y = grid_props.get("gridY")
        
        if not all([office, grid_x, grid_y]):
            print(f"Warning: Could not get grid data for {location}, skipping")
            continue
        
        # Fetch active alerts for the state (extract from location)
        try:
            state = location.split(",")[-1].strip() if "," in location else None
            if state and len(state) == 2:
                alerts_data = client.get_active_alerts(state)
                for feature in alerts_data.get("features", [])[:FETCH_LIMIT]:
                    weather_docs.append(normalize_alert(feature, location))
        except Exception as e:
            print(f"Warning: Could not fetch alerts for {location}: {e}")
        
        # Fetch forecast
        try:
            forecast_data = client.get_forecast(office, grid_x, grid_y)
            for period in forecast_data.get("properties", {}).get("periods", [])[:FETCH_LIMIT]:
                weather_docs.append(normalize_forecast(period, location, office, grid_x, grid_y))
        except Exception as e:
            print(f"Warning: Could not fetch forecast for {location}: {e}")
        
    except Exception as e:
        print(f"Error processing location '{location}': {e}")
        continue

print(f"\n✅ Fetched {len(weather_docs)} weather documents")

# Count by type
types_count = {}
for doc in weather_docs:
    doc_type = doc.get('source_type', 'unknown')
    types_count[doc_type] = types_count.get(doc_type, 0) + 1

print(f"\nBreakdown by type:")
for doc_type, count in types_count.items():
    print(f"  - {doc_type}: {count}")

if weather_docs:
    print(f"\nSample document:")
    sample = weather_docs[0]
    print(f"  Location: {sample['location']}")
    print(f"  Type: {sample['source_type']}")
    print(f"  Headline: {sample['headline']}")

# COMMAND ----------

# DBTITLE 1,Insert to Lakebase
# MAGIC %md
# MAGIC ## Insert Weather Documents to Lakebase
# MAGIC
# MAGIC Batch insert using psycopg2 with ON CONFLICT DO UPDATE for upsert behavior.

# COMMAND ----------

# DBTITLE 1,Upsert weather documents
import json
import psycopg2
from psycopg2.extras import execute_batch

if not weather_docs:
    print("⚠️ No weather documents to insert")
else:
    print(f"Inserting {len(weather_docs)} weather documents into {WEATHER_TABLE_NAME}...\n")
    
    try:
        # Connect to Lakebase
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password,
            sslmode='require'
        )
        cursor = conn.cursor()
        
        # Upsert SQL with ON CONFLICT DO UPDATE
        upsert_sql = f"""
            INSERT INTO {WEATHER_TABLE_NAME} (
                id, location, source_type, headline, narrative_text, issued_at, payload, synced_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                location = EXCLUDED.location,
                source_type = EXCLUDED.source_type,
                headline = EXCLUDED.headline,
                narrative_text = EXCLUDED.narrative_text,
                issued_at = EXCLUDED.issued_at,
                payload = EXCLUDED.payload,
                synced_at = NOW()
        """
        
        # Prepare data tuples for batch insert
        data_tuples = [
            (
                doc['id'],
                doc['location'],
                doc['source_type'],
                doc['headline'],
                doc['narrative_text'],
                doc['issued_at'],
                json.dumps(doc['payload'])
            )
            for doc in weather_docs
        ]
        
        # Execute batch insert
        execute_batch(cursor, upsert_sql, data_tuples)
        conn.commit()
        
        inserted_count = len(data_tuples)
        print(f"✅ Successfully upserted {inserted_count} weather documents\n")
        
        # Show breakdown by type
        types_count = {}
        for doc in weather_docs:
            doc_type = doc.get('source_type', 'unknown')
            types_count[doc_type] = types_count.get(doc_type, 0) + 1
        
        print("Documents by type:")
        for doc_type, count in types_count.items():
            print(f"  - {doc_type}: {count}")
        
        # Verify insertion
        cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_TABLE_NAME}")
        total_count = cursor.fetchone()[0]
        print(f"\nTotal documents in table: {total_count}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Error inserting documents: {e}")
        import traceback
        traceback.print_exc()

# COMMAND ----------

import pandas as pd
import psycopg2

# Load news documents using psycopg2
conn = psycopg2.connect(
    host=db_host,
    port=db_port,
    dbname=db_name,
    user=db_user,
    password=db_password,
    sslmode='require'
)

try:
    # Query with embedding_text computed
    query = f"""
        SELECT 
            id,
            location,
            source_type,
            headline,
            narrative_text,
            issued_at,
            payload,
            TRIM(CONCAT(COALESCE(location, ''), '. ', COALESCE(narrative_text, ''))) AS embedding_text
        FROM {WEATHER_TABLE_NAME}
        WHERE TRIM(CONCAT(COALESCE(location, ''), '. ', COALESCE(narrative_text, ''))) IS NOT NULL
          AND TRIM(CONCAT(COALESCE(location, ''), '. ', COALESCE(narrative_text, ''))) != ''
    """
    
    weather_df = pd.read_sql_query(query, conn)
    print(f"Loaded {len(weather_df)} news documents from {WEATHER_TABLE_NAME}")
    display(weather_df.head(5))
finally:
    conn.close()

# COMMAND ----------

# MAGIC %md 
# MAGIC # Compute embeddings
# MAGIC Loads the sentence-transformers model once and applies it in batches to the news documents.

# COMMAND ----------

import os
import pandas as pd
from sentence_transformers import SentenceTransformer

# Set up HuggingFace cache
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["TRANSFORMERS_CACHE"] = "/tmp/.cache/huggingface"
os.environ["HF_HUB_CACHE"] = "/tmp/.cache/huggingface"

print(f"Loading embedding model {EMBEDDING_MODEL_NAME}...")
model = SentenceTransformer(EMBEDDING_MODEL_NAME, cache_folder="/tmp/.cache/huggingface")

# Compute embeddings in batches for memory efficiency
print("Computing embeddings...")
batch_size = 32
all_embeddings = []

for i in range(0, len(weather_df), batch_size):
    batch = weather_df.iloc[i:i+batch_size]
    vectors = model.encode(batch["embedding_text"].tolist(), show_progress_bar=False)
    all_embeddings.extend(vectors.tolist())
    if (i + batch_size) % 128 == 0:
        print(f"  Processed {min(i + batch_size, len(weather_df))}/{len(weather_df)} documents")

# Create embeddings DataFrame
embeddings_df = pd.DataFrame({
    "id": weather_df["id"],
    "location": weather_df["location"],
    "headline": weather_df["headline"],
    "published_utc": weather_df["issued_at"].astype(str),
    "embedding": all_embeddings,
})

print(f"Computed {len(embeddings_df)} embeddings using {EMBEDDING_MODEL_NAME}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ensure the pgvector destination table exists
# MAGIC The pgvector extension must be enabled and the destination table created with the correct vector dimension before inserting embeddings.

# COMMAND ----------

# Before running the cells below, ensure you've manually run:
#   sql/02_setup_embeddings_table.sql
# Replace {{EMBEDDING_DIM}} in that file with the value below:
print(f"Required EMBEDDING_DIM for SQL setup: {EMBEDDING_DIM}")
print(f"Table name: {EMBEDDINGS_TABLE_NAME}")
print("\nRun sql/02_setup_embeddings_table.sql in your Lakebase database before continuing.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Upsert embeddings into Lakebase
# MAGIC Written in batches via psycopg2's `executemany` for throughput. Each embedding is cast to Postgres' `vector` type via `::vector`.

# COMMAND ----------

import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime

# Add model_name and embedded_at columns
embeddings_df['model_name'] = EMBEDDING_MODEL_NAME
embeddings_df['embedded_at'] = datetime.now()

embeddings_rows = embeddings_df.to_dict('records')

if len(embeddings_rows) > 0:
    print(f"Inserting {len(embeddings_rows)} embeddings into {EMBEDDINGS_TABLE_NAME}...")
    
    # Build connection from parsed URL
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode='require'
    )
    
    try:
        cursor = conn.cursor()
        
        # Prepare data tuples for batch insert
        # Format embedding as PostgreSQL array literal: '{val1,val2,...}'
        insert_data = [
            (
                row['id'],
                row['location'],
                row['headline'],
                str(row['published_utc']) if row['published_utc'] else None,
                '{' + ','.join(str(float(x)) for x in row['embedding']) + '}',
                row['model_name'],
                row['embedded_at']
            )
            for row in embeddings_rows
        ]
        
        # Batch insert with ON CONFLICT DO NOTHING for deduplication
        insert_sql = f"""
            INSERT INTO {EMBEDDINGS_TABLE_NAME} (
                id, location, headline, published_utc, embedding, model_name, embedded_at
            ) VALUES %s
            ON CONFLICT (id) DO NOTHING
        """
        
        # execute_values is much faster than individual INSERTs
        template = "(%s, %s, %s, %s, %s::double precision[], %s, %s)"
        execute_values(cursor, insert_sql, insert_data, template=template, page_size=100)
        
        conn.commit()
        inserted_count = cursor.rowcount
        print(f"✅ Successfully inserted {inserted_count} new embeddings")
        print(f"   (Duplicates were skipped via ON CONFLICT DO NOTHING)")
        print("\nIMPORTANT: Run this SQL in your Lakebase database to cast arrays to vectors:")
        print(f"  UPDATE {EMBEDDINGS_TABLE_NAME} SET embedding = embedding::vector WHERE embedding IS NOT NULL;")
        
    finally:
        cursor.close()
        conn.close()
else:
    print("No embeddings to write.")

# COMMAND ----------

# DBTITLE 1,Test Vector Search API
# MAGIC %md
# MAGIC ## Test Vector Search API
# MAGIC
# MAGIC Once the Flask app is running, you can test the vector search endpoint.
# MAGIC
# MAGIC The web UI is available at `http://localhost:5000`

# COMMAND ----------

# DBTITLE 1,Test API with requests
import requests
import json

# Test the vector search endpoint
API_URL = "http://localhost:5000/search"

test_queries = [
    "heat advisory in Chicago",
    "fog warning",
    "severe weather alerts",
    "thunderstorm forecast"
]

print("Testing Vector Search API")
print("=" * 80)

for query in test_queries:
    print(f"\n🔍 Query: '{query}'")
    
    try:
        response = requests.post(
            API_URL,
            json={"query": query, "top_k": 3},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data['success']:
                print(f"   Found {data['count']} results:\n")
                for i, result in enumerate(data['results'], 1):
                    similarity_pct = result['similarity'] * 100
                    print(f"   {i}. [{similarity_pct:.1f}%] {result['headline']} - {result['location']}")
                    print(f"      {result['chunk_text'][:150]}...\n")
            else:
                print(f"   ❌ Error: {data['error']}")
        else:
            print(f"   ❌ HTTP {response.status_code}: {response.text}")
    
    except requests.exceptions.ConnectionError:
        print("   ⚠️  Flask server not running. Start it with: python app.py")
        break
    except Exception as e:
        print(f"   ❌ Error: {e}")