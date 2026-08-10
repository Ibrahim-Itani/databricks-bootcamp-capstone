# Weather MCP Server - DataExpert Bootcamp Capstone

A Model Context Protocol (MCP) server that provides weather data and intelligent travel recommendations through the Databricks AI platform. This server integrates real-time weather data from Open-Meteo and exposes tools the Databricks Assistant can call via the MCP protocol.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Databricks AI Platform                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Databricks Assistant (Genie)                 │  │
│  │              (MCP Client)                                 │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│                            │ MCP Protocol (SSE/HTTP)             │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │          Weather MCP Server (FastMCP)                     │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  MCP Tools:                                         │  │  │
│  │  │  • get_current_weather(location)                    │  │  │
│  │  │  • get_forecast(location, days)                     │  │  │
│  │  │  • get_travel_recommendation(location, date)        │  │  │
│  │  │  • vector_search(query, limit)                      │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  │               │                            │                │  │
│  └───────────────┼────────────────────────────┼────────────────┘  │
│                  │                            │                   │
│     ┌────────────▼────────────┐   ┌──────────▼────────────────┐  │
│     │   weather_broker.py     │   │   Lakebase Postgres       │  │
│     │   (Weather API Logic)   │   │   (pgvector)              │  │
│     └────────────┬────────────┘   │                           │  │
│                  │                │  • weather_documents       │  │
│                  │                │  • weather_documents_      │  │
│                  │                │    embeddings (384-dim)    │  │
│                  │                │  • mcp_tool_traces         │  │
│                  │                │    (observability)         │  │
│                  │                └───────────────────────────┘  │
└──────────────────┼──────────────────────────────────────────────┘
                   │
                   │ HTTPS
                   │
         ┌─────────▼──────────┐
         │   Open-Meteo API   │
         │   (Free, No Auth)  │
         │                    │
         │ • Geocoding        │
         │ • Current Weather  │
         │ • 16-day Forecast  │
         └────────────────────┘
```

## MCP Tools

The server exposes four tools through the Model Context Protocol:

### 1. `get_current_weather(location: str)`
Retrieves current weather conditions for a specified location.

**Parameters:**
- `location` (str): City name and state (e.g., "San Francisco, CA")

**Returns:**
- `temperature_f`: Current temperature in Fahrenheit
