import os
import logging
import uuid
import time
import json
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable

from fastmcp import FastMCP
from sentence_transformers import SentenceTransformer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import weather_broker
import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

# Load embedding model once at startup
_embedding_model = None

def get_embedding_model():
    """Lazy-load the embedding model (expensive operation, only on first use)."""
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embedding_model

# Table names from environment variables
WEATHER_TABLE_NAME = os.environ.get("WEATHER_TABLE_NAME", "weather_documents")
EMBEDDINGS_TABLE_NAME = os.environ.get("EMBEDDINGS_TABLE_NAME", "weather_documents_embeddings")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Context variable to store request headers for accessing end-user identity
_request_context: ContextVar[dict] = ContextVar('request_context', default={})

# Context variable to store session ID for tracing
_session_id: ContextVar[str] = ContextVar('session_id', default=None)

# Table name for tracing
TRACING_TABLE_NAME = os.environ.get("TRACING_TABLE_NAME", "mcp_tool_traces")

def _get_end_user_email() -> str:
    """Get the actual end user's email from request headers, or fallback to service principal."""
    # Try to get from X-Forwarded-User header (Databricks App context)
    headers = _request_context.get()
    forwarded_user = headers.get('x-forwarded-user')
    if forwarded_user:
        return forwarded_user
    
    # Fallback: use service principal (local development or non-App contexts)
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    return w.current_user.me().user_name

def _get_or_create_session_id() -> str:
    """Get the current session ID or generate a new one."""
    session_id = _session_id.get()
    if session_id is None:
        session_id = str(uuid.uuid4())
        _session_id.set(session_id)
        logger.info(f"Generated new session ID: {session_id}")
    return session_id

def _log_trace(tool_name: str, parameters: dict, result: Any, duration_ms: float, error: str = None):
    """Log a tool invocation to the tracing table."""
    try:
        session_id = _get_or_create_session_id()
        user_email = _get_end_user_email()
        
        # Serialize result and parameters to JSON
        params_json = json.dumps(parameters)
        result_json = json.dumps(result) if result is not None else None
        
        trace_data = {
            "session_id": session_id,
            "tool_name": tool_name,
            "user_email": user_email,
            "parameters": params_json,
            "result": result_json,
            "duration_ms": duration_ms,
            "error": error,
            "timestamp": "NOW()"
        }
        
        # Insert into tracing table
        sql = f"""
            INSERT INTO {TRACING_TABLE_NAME} 
            (session_id, tool_name, user_email, parameters, result, duration_ms, error, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        
        lakebase.run_write(
            sql,
            (
                trace_data["session_id"],
                trace_data["tool_name"],
                trace_data["user_email"],
                trace_data["parameters"],
                trace_data["result"],
                trace_data["duration_ms"],
                trace_data["error"]
            )
        )
        logger.info(f"Logged trace for {tool_name} in session {session_id}")
    except Exception as e:
        # Don't fail the actual tool call if tracing fails
        logger.error(f"Failed to log trace: {e}")

def trace_tool(func: Callable) -> Callable:
    """Decorator to automatically trace MCP tool invocations."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        start_time = time.time()
        error = None
        result = None
        
        try:
            # Capture parameters
            parameters = {}
            # Get parameter names from function signature
            import inspect
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())
            
            # Map positional args to parameter names
            for i, arg in enumerate(args):
                if i < len(param_names):
                    parameters[param_names[i]] = arg
            
            # Add keyword args
            parameters.update(kwargs)
            
            # Execute the actual tool
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            error = str(e)
            logger.error(f"Tool {tool_name} failed: {e}")
            raise
        finally:
            # Log the trace
            duration_ms = (time.time() - start_time) * 1000
            _log_trace(tool_name, parameters, result, duration_ms, error)
    
    return wrapper


mcp = FastMCP("Mateo-weather-recommendation")

class RequestContextMiddleware(BaseHTTPMiddleware):
    """Middleware to capture HTTP headers and generate session IDs."""
    async def dispatch(self, request: Request, call_next):
        # Capture headers that Databricks injects with user identity
        headers = {
            'x-forwarded-user': request.headers.get('x-forwarded-user'),
            'x-forwarded-email': request.headers.get('x-forwarded-email'),
        }
        _request_context.set(headers)
        
        # Generate a new session ID for this request if not already set
        # In a real MCP server, you might want to tie this to a conversation ID
        # from the MCP protocol headers
        if _session_id.get() is None:
            new_session_id = str(uuid.uuid4())
            _session_id.set(new_session_id)
            logger.info(f"New session started: {new_session_id}")
        
        response = await call_next(request)
        return response
@mcp.tool
@trace_tool
def get_current_weather(location: str) -> dict:
    """
    Get the weather conditions from Open-Mateo.

    Args:
        location: city name and state.

    Returns:
        A dict with temperature, conditions, humidity, wind.
    """
    return weather_broker.get_current_weather(location)

@mcp.tool
@trace_tool
def get_forecast(location: str, days: float) -> dict:
    """
    Gets a multi day forecast for the next N days.
    
    Args:
        location: city name and state.
        days: number of days required for the forecast.
        
    Returns:
        A dict with temp high/low, precipitation chance, conditions.
    """
    return weather_broker.get_forecast(location, days)

@mcp.tool
@trace_tool
def get_travel_recommendation(location: str, date: str) -> dict:
    """
    Get travel recommendations for a location on a specific date.
    
    Args:
        location: city name and state.
        date: travel date in YYYY-MM-DD format.
        
    Returns:
        A dict with travel assessment, recommendations, and items to bring.
    """
    return weather_broker.get_travel_recommendation(location, date)


@mcp.tool
@trace_tool
def vector_search(query: str, limit: int = 10) -> dict:
    """
    Semantic search over weather documents using vector embeddings.
    
    Accepts a text query, computes its embedding, and returns the most similar
    weather documents from Lakebase using pgvector's cosine similarity.
    
    Args:
        query: Natural language search query (e.g. "severe storm warnings" or "heat advisory")
        limit: Maximum number of results to return (default 10)
    
    Returns:
        A dict with query, documents (id, location, headline, narrative_text, source_type, 
        published_utc, issued_at, payload, model_name, similarity score), and embedding model name
    """
    if not query or not query.strip():
        return {"error": "Query text is required"}
    
    try:
        # Compute embedding for the query
        model = get_embedding_model()
        query_embedding = model.encode(query)
        
        # Convert to list for JSON serialization and postgres array format
        embedding_list = query_embedding.tolist()
        
        # Search document-level embeddings
        doc_results = lakebase.run_query(
            f"""
            SELECT 
                e.id,
                e.location,
                e.headline,
                e.published_utc,
                e.model_name,
                e.embedded_at,
                d.source_type,
                d.narrative_text,
                d.issued_at,
                d.payload,
                1 - (e.embedding <=> %s::vector) as similarity
            FROM {EMBEDDINGS_TABLE_NAME} e
            LEFT JOIN {WEATHER_TABLE_NAME} d ON e.id = d.id
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            (str(embedding_list), str(embedding_list), limit),
        )
        return {
            "query": query,
            "documents": doc_results,
            "model": EMBEDDING_MODEL
        }
        
    except Exception as e:
        logger.exception("Vector search failed")
        return {"error": str(e)}


# ==============================================================================
# USER ACTIONS TOOLS - Persistent storage for favorites, trips, notes, alerts
# ==============================================================================

@mcp.tool
@trace_tool
def save_favorite_location(
    location_name: str,
    latitude: float = None,
    longitude: float = None,
    nickname: str = None,
    category: str = None,
    notes: str = None
) -> dict:
    """
    Save a location to the user's favorites list.
    
    Args:
        location_name: Name of the location (e.g., "Paris, France")
        latitude: Latitude coordinate (optional)
        longitude: Longitude coordinate (optional)
        nickname: User-friendly name for the location (optional)
        category: Category like 'home', 'work', 'vacation', 'family' (optional)
        notes: Additional notes about the location (optional)
    
    Returns:
        Dict with success status and location ID, or error message
    """
    return weather_broker.save_favorite_location(
        location_name, latitude, longitude, nickname, category, notes
    )


@mcp.tool
@trace_tool
def get_favorite_locations(category: str = None) -> dict:
    """
    Retrieve the user's favorite locations.
    
    Args:
        category: Filter by category (optional). Categories: 'home', 'work', 'vacation', 'family'
    
    Returns:
        List of favorite locations with details
    """
    locations = weather_broker.get_favorite_locations(category)
    return {"locations": locations, "count": len(locations)}


@mcp.tool
@trace_tool
def create_trip(
    trip_name: str,
    destination: str,
    start_date: str = None,
    end_date: str = None,
    travelers_count: int = None,
    budget_amount: float = None,
    weather_preferences: dict = None,
    activities: list = None
) -> dict:
    """
    Create a new trip with travel plans.
    
    Args:
        trip_name: Name of the trip (required)
        destination: Destination location (required)
        start_date: Trip start date in YYYY-MM-DD format (optional)
        end_date: Trip end date in YYYY-MM-DD format (optional)
        travelers_count: Number of travelers (optional)
        budget_amount: Budget in dollars (optional)
        weather_preferences: Dict with preferences like {'ideal_temp_min': 65, 'ideal_temp_max': 85, 'avoid_rain': True} (optional)
        activities: List of planned activities (optional)
    
    Returns:
        Dict with success status and trip ID, or error message
    """
    return weather_broker.create_trip(
        trip_name, destination, start_date, end_date,
        travelers_count, budget_amount, weather_preferences, activities
    )


@mcp.tool
@trace_tool
def get_trips(status: str = None) -> dict:
    """
    Retrieve the user's trips.
    
    Args:
        status: Filter by status (optional). Valid values: 'planned', 'active', 'completed', 'cancelled'
    
    Returns:
        List of trips with details
    """
    trips = weather_broker.get_trips(status)
    return {"trips": trips, "count": len(trips)}


@mcp.tool
@trace_tool
def create_note(
    content: str,
    title: str = None,
    location: str = None,
    trip_id: str = None,
    note_type: str = 'general',
    tags: list = None,
    is_pinned: bool = False
) -> dict:
    """
    Create a new note for a location or trip.
    
    Args:
        content: Note content (required)
        title: Note title (optional)
        location: Associated location (optional)
        trip_id: Associated trip UUID (optional)
        note_type: Type of note - 'general', 'recommendation', 'warning', or 'reminder' (default: 'general')
        tags: List of tags for categorization (optional)
        is_pinned: Pin note to top (default: False)
    
    Returns:
        Dict with success status and note ID, or error message
    """
    return weather_broker.create_note(
        content, title, location, trip_id, note_type, tags, is_pinned
    )


@mcp.tool
@trace_tool
def get_notes(
    trip_id: str = None,
    location: str = None,
    note_type: str = None,
    pinned_only: bool = False
) -> dict:
    """
    Retrieve the user's notes with optional filters.
    
    Args:
        trip_id: Filter by trip UUID (optional)
        location: Filter by location (optional)
        note_type: Filter by type - 'general', 'recommendation', 'warning', 'reminder' (optional)
        pinned_only: Only return pinned notes (default: False)
    
    Returns:
        List of notes with details
    """
    notes = weather_broker.get_notes(trip_id, location, note_type, pinned_only)
    return {"notes": notes, "count": len(notes)}


@mcp.tool
@trace_tool
def create_alert(
    location: str,
    alert_type: str,
    alert_condition: dict,
    trip_id: str = None,
    notification_method: str = 'in_app',
    expires_at: str = None
) -> dict:
    """
    Create a new alert for weather conditions or custom events.
    
    Args:
        location: Location to monitor (required)
        alert_type: Type of alert - 'weather', 'price', 'reminder', or 'custom' (required)
        alert_condition: Dict with conditions, e.g., {'temp_above': 90, 'weather_events': ['thunderstorm']} (required)
        trip_id: Associated trip UUID (optional)
        notification_method: Notification method - 'in_app', 'email', or 'both' (default: 'in_app')
        expires_at: Optional expiration datetime in ISO format (optional)
    
    Returns:
        Dict with success status and alert ID, or error message
    """
    return weather_broker.create_alert(
        location, alert_type, alert_condition, trip_id, notification_method, expires_at
    )


@mcp.tool
@trace_tool
def get_alerts(
    location: str = None,
    alert_type: str = None,
    active_only: bool = True
) -> dict:
    """
    Retrieve the user's alerts.
    
    Args:
        location: Filter by location (optional)
        alert_type: Filter by type - 'weather', 'price', 'reminder', 'custom' (optional)
        active_only: Only return active alerts (default: True)
    
    Returns:
        List of alerts with details
    """
    alerts = weather_broker.get_alerts(location, alert_type, active_only)
    return {"alerts": alerts, "count": len(alerts)}


if __name__ == "__main__":
    # Add middleware to capture request headers for end-user identity
    # This must be done before mcp.run() is called
    if hasattr(mcp, 'app') and mcp.app is not None:
        mcp.app.add_middleware(RequestContextMiddleware)
    
    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # streamable-http is the transport Databricks' MCP client/gateway expects
    # (see the "Host your own MCP" doc linked in the module docstring above).
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)

    
