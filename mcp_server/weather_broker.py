"""
Open-Meteo weather engine backing the weather-mcp-server.

Functionality:
1- Current conditions - get_current_weather(location) - temperature, conditions, humidity, wind
2- Forecast - get_forecast(location, days) - multi-day forecast with temp high/low, precipitation
3- Travel recommendation - get_travel_recommendation(location, date) - weather-based travel advice
"""

import base64
import os
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import requests

from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor

import lakebase

logger = logging.getLogger(__name__)
_w = WorkspaceClient()

# Open-Meteo API endpoints (free, no API key required)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

def _geocode_location(location: str) -> Dict[str, float]:
    """
    Convert a location name (e.g., 'San Francisco, CA') to latitude/longitude.
    
    Args:
        location: City name, optionally with state/country
    
    Returns:
        Dict with 'latitude', 'longitude', and 'name'
    
    Raises:
        ValueError: If location cannot be geocoded
    """
    try:
        response = requests.get(
            GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if not data.get("results"):
            raise ValueError(f"Location '{location}' not found")
        
        result = data["results"][0]
        return {
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "name": result["name"]
        }
    except Exception as e:
        logger.error(f"Geocoding failed for '{location}': {e}")
        raise ValueError(f"Could not geocode location '{location}': {str(e)}")

def get_current_weather(location: str) -> Dict[str, Any]:
    """
    Get current weather conditions for a location.
    
    Args:
        location: City name and state (e.g., 'San Francisco, CA')
    
    Returns:
        Dict with temperature, conditions, humidity, and wind speed
    """
    try:
        # Geocode the location
        coords = _geocode_location(location)
        
        # Fetch current weather from Open-Meteo
        response = requests.get(
            WEATHER_URL,
            params={
                "latitude": coords["latitude"],
                "longitude": coords["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph"
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        current = data.get("current", {})
        
        # Map weather codes to conditions
        weather_code = current.get("weather_code", 0)
        conditions = _weather_code_to_description(weather_code)
        
        return {
            "location": coords["name"],
            "temperature_f": current.get("temperature_2m"),
            "conditions": conditions,
            "humidity_percent": current.get("relative_humidity_2m"),
            "wind_speed_mph": current.get("wind_speed_10m"),
            "timestamp": current.get("time")
        }
    except Exception as e:
        logger.error(f"Failed to get current weather for '{location}': {e}")
        return {"error": str(e)}

def get_forecast(location: str, days: float) -> Dict[str, Any]:
    """
    Get multi-day weather forecast.
    
    Args:
        location: City name and state (e.g., 'Boston, MA')
        days: Number of days to forecast (max 16)
    
    Returns:
        Dict with daily forecasts including temp high/low, precipitation, conditions
    """
    try:
        # Geocode the location
        coords = _geocode_location(location)
        
        # Ensure days is within valid range
        days = max(1, min(int(days), 16))
        
        # Fetch forecast from Open-Meteo
        response = requests.get(
            WEATHER_URL,
            params={
                "latitude": coords["latitude"],
                "longitude": coords["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "temperature_unit": "fahrenheit",
                "forecast_days": days
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        temp_max = daily.get("temperature_2m_max", [])
        temp_min = daily.get("temperature_2m_min", [])
        precip_prob = daily.get("precipitation_probability_max", [])
        weather_codes = daily.get("weather_code", [])
        
        forecast_days = []
        for i in range(len(dates)):
            forecast_days.append({
                "date": dates[i],
                "temp_high_f": temp_max[i],
                "temp_low_f": temp_min[i],
                "precipitation_probability_percent": precip_prob[i],
                "conditions": _weather_code_to_description(weather_codes[i])
            })
        
        return {
            "location": coords["name"],
            "forecast_days": forecast_days
        }
    except Exception as e:
        logger.error(f"Failed to get forecast for '{location}': {e}")
        return {"error": str(e)}

def get_travel_recommendation(location: str, date: str) -> Dict[str, Any]:
    """
    Provide travel recommendations based on weather forecast.
    
    Args:
        location: City name and state
        date: Target date (YYYY-MM-DD format)
    
    Returns:
        Dict with recommendations, weather summary, and what to bring
    """
    try:
        # Parse the target date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        today = datetime.now().date()
        days_ahead = (target_date - today).days
        
        if days_ahead < 0:
            return {"error": "Cannot provide recommendations for past dates"}
        
        if days_ahead > 16:
            return {"error": "Forecast only available up to 16 days in advance"}
        
        # Get the forecast
        forecast_data = get_forecast(location, days_ahead + 1)
        
        if "error" in forecast_data:
            return forecast_data
        
        # Find the forecast for the target date
        target_forecast = None
        for day in forecast_data.get("forecast_days", []):
            if day["date"] == date:
                target_forecast = day
                break
        
        if not target_forecast:
            return {"error": f"No forecast data available for {date}"}
        
        # Build recommendations based on weather conditions
        recommendations = []
        items_to_bring = []
        
        # Precipitation recommendations
        precip_prob = target_forecast.get("precipitation_probability_percent", 0)
        if precip_prob > 60:
            recommendations.append("High chance of precipitation - consider rescheduling outdoor activities")
            items_to_bring.extend(["umbrella", "rain jacket", "waterproof shoes"])
        elif precip_prob > 40:
            recommendations.append("Moderate chance of rain - be prepared for wet weather")
            items_to_bring.append("umbrella")
        elif precip_prob > 20:
            recommendations.append("Slight chance of rain - pack an umbrella just in case")
        else:
            recommendations.append("Low chance of precipitation - great day for outdoor activities")
        
        # Temperature recommendations
        temp_high = target_forecast.get("temp_high_f", 70)
        temp_low = target_forecast.get("temp_low_f", 50)
        
        if temp_high > 85:
            recommendations.append("Hot weather expected - stay hydrated and avoid prolonged sun exposure")
            items_to_bring.extend(["sunscreen", "hat", "water bottle"])
        elif temp_high > 75:
            recommendations.append("Warm weather - comfortable for most outdoor activities")
            items_to_bring.append("sunscreen")
        elif temp_high < 40:
            recommendations.append("Cold weather - dress in warm layers")
            items_to_bring.extend(["heavy coat", "gloves", "warm hat"])
        elif temp_high < 55:
            recommendations.append("Cool weather - bring a jacket or sweater")
            items_to_bring.append("jacket")
        
        if temp_high - temp_low > 25:
            recommendations.append("Large temperature swing expected - dress in layers")
        
        # Overall travel assessment
        conditions = target_forecast.get("conditions", "")
        if "clear" in conditions.lower() or "sunny" in conditions.lower():
            overall = "Excellent travel conditions"
        elif precip_prob > 60 or temp_high > 90 or temp_high < 32:
            overall = "Challenging travel conditions - plan accordingly"
        else:
            overall = "Good travel conditions with minor considerations"
        
        return {
            "location": forecast_data.get("location"),
            "date": date,
            "overall_assessment": overall,
            "weather_summary": {
                "conditions": conditions,
                "high_temp_f": temp_high,
                "low_temp_f": temp_low,
                "precipitation_probability_percent": precip_prob
            },
            "recommendations": recommendations,
            "items_to_bring": list(set(items_to_bring))  # Remove duplicates
        }
    except Exception as e:
        logger.error(f"Failed to get travel recommendation for '{location}' on '{date}': {e}")
        return {"error": str(e)}

def _weather_code_to_description(code: int) -> str:
    """
    Convert WMO weather code to human-readable description.
    
    Based on Open-Meteo's weather code documentation:
    https://open-meteo.com/en/docs
    """
    weather_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail"
    }
    return weather_codes.get(code, f"Unknown (code {code})")


# ==============================================================================
# USER ACTIONS API - Persistent storage for favorites, trips, notes, alerts
# ==============================================================================

def _get_current_user_id() -> str:
    """Get the current Databricks user's email as user ID."""
    return _w.current_user.me().user_name

def _get_lakebase_connection():
    """Get a direct psycopg2 connection to Lakebase."""
    import psycopg2
    from urllib.parse import urlparse
    
    # Get the Lakebase URL from the secret
    lakebase_url = lakebase._lakebase_url()
    
    # Parse the connection URL
    parsed = urlparse(lakebase_url)
    
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip('/'),
        user=parsed.username,
        password=parsed.password,
        sslmode='require',
        cursor_factory=RealDictCursor
    )

def save_favorite_location(
    location_name: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    nickname: Optional[str] = None,
    category: Optional[str] = None,
    notes: Optional[str] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Save a location to user's favorites.
    
    Args:
        location_name: Name of the location (e.g., "Paris, France")
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        nickname: User-friendly name for the location
        category: Category like 'home', 'work', 'vacation', 'family'
        notes: Additional notes about the location
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and location ID
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_favorite_locations 
                (user_id, location_name, latitude, longitude, nickname, category, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, location_name) 
                DO UPDATE SET
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    nickname = EXCLUDED.nickname,
                    category = EXCLUDED.category,
                    notes = EXCLUDED.notes,
                    updated_at = NOW(),
                    is_active = TRUE
                RETURNING id, location_name, created_at, updated_at
            """, (user_id, location_name, latitude, longitude, nickname, category, notes))
            
            result = cur.fetchone()
            conn.commit()
            
            return {
                'success': True,
                'id': str(result['id']),
                'location_name': result['location_name'],
                'created_at': result['created_at'].isoformat(),
                'updated_at': result['updated_at'].isoformat()
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to save favorite location: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def get_favorite_locations(
    category: Optional[str] = None,
    user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve user's favorite locations.
    
    Args:
        category: Filter by category
        user_id: User ID (defaults to current user)
    
    Returns:
        List of favorite locations
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            if category:
                cur.execute("""
                    SELECT id, location_name, latitude, longitude, nickname, 
                           category, notes, created_at, updated_at
                    FROM user_favorite_locations
                    WHERE user_id = %s AND category = %s AND is_active = TRUE
                    ORDER BY updated_at DESC
                """, (user_id, category))
            else:
                cur.execute("""
                    SELECT id, location_name, latitude, longitude, nickname, 
                           category, notes, created_at, updated_at
                    FROM user_favorite_locations
                    WHERE user_id = %s AND is_active = TRUE
                    ORDER BY updated_at DESC
                """, (user_id,))
            
            results = cur.fetchall()
            return [
                {
                    'id': str(row['id']),
                    'location_name': row['location_name'],
                    'latitude': float(row['latitude']) if row['latitude'] else None,
                    'longitude': float(row['longitude']) if row['longitude'] else None,
                    'nickname': row['nickname'],
                    'category': row['category'],
                    'notes': row['notes'],
                    'created_at': row['created_at'].isoformat(),
                    'updated_at': row['updated_at'].isoformat()
                }
                for row in results
            ]
    except Exception as e:
        logger.error(f"Failed to get favorite locations: {e}")
        return []
    finally:
        conn.close()


def create_trip(
    trip_name: str,
    destination: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    travelers_count: Optional[int] = None,
    budget_amount: Optional[float] = None,
    weather_preferences: Optional[Dict] = None,
    activities: Optional[List[str]] = None,
    metadata: Optional[Dict] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new trip.
    
    Args:
        trip_name: Name of the trip
        destination: Destination location
        start_date: Trip start date (YYYY-MM-DD)
        end_date: Trip end date (YYYY-MM-DD)
        travelers_count: Number of travelers
        budget_amount: Budget in dollars
        weather_preferences: Dict with preferences like {'ideal_temp': 75, 'avoid_rain': True}
        activities: List of planned activities
        metadata: Additional trip metadata
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and trip ID
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_trips 
                (user_id, trip_name, destination, start_date, end_date, 
                 travelers_count, budget_amount, weather_preferences, activities, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, trip_name, destination, created_at
            """, (
                user_id, trip_name, destination, start_date, end_date,
                travelers_count, budget_amount,
                json.dumps(weather_preferences) if weather_preferences else None,
                json.dumps(activities) if activities else None,
                json.dumps(metadata) if metadata else None
            ))
            
            result = cur.fetchone()
            conn.commit()
            
            return {
                'success': True,
                'id': str(result['id']),
                'trip_name': result['trip_name'],
                'destination': result['destination'],
                'created_at': result['created_at'].isoformat()
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create trip: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def get_trips(
    status: Optional[str] = None,
    user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve user's trips.
    
    Args:
        status: Filter by status ('planned', 'active', 'completed', 'cancelled')
        user_id: User ID (defaults to current user)
    
    Returns:
        List of trips
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute("""
                    SELECT id, trip_name, destination, start_date, end_date, status,
                           travelers_count, budget_amount, weather_preferences, 
                           activities, metadata, created_at, updated_at
                    FROM user_trips
                    WHERE user_id = %s AND status = %s
                    ORDER BY start_date DESC NULLS LAST, created_at DESC
                """, (user_id, status))
            else:
                cur.execute("""
                    SELECT id, trip_name, destination, start_date, end_date, status,
                           travelers_count, budget_amount, weather_preferences, 
                           activities, metadata, created_at, updated_at
                    FROM user_trips
                    WHERE user_id = %s
                    ORDER BY start_date DESC NULLS LAST, created_at DESC
                """, (user_id,))
            
            results = cur.fetchall()
            return [
                {
                    'id': str(row['id']),
                    'trip_name': row['trip_name'],
                    'destination': row['destination'],
                    'start_date': row['start_date'].isoformat() if row['start_date'] else None,
                    'end_date': row['end_date'].isoformat() if row['end_date'] else None,
                    'status': row['status'],
                    'travelers_count': row['travelers_count'],
                    'budget_amount': float(row['budget_amount']) if row['budget_amount'] else None,
                    'weather_preferences': row['weather_preferences'],
                    'activities': row['activities'],
                    'metadata': row['metadata'],
                    'created_at': row['created_at'].isoformat(),
                    'updated_at': row['updated_at'].isoformat()
                }
                for row in results
            ]
    except Exception as e:
        logger.error(f"Failed to get trips: {e}")
        return []
    finally:
        conn.close()


def create_note(
    content: str,
    title: Optional[str] = None,
    location: Optional[str] = None,
    trip_id: Optional[str] = None,
    note_type: str = 'general',
    tags: Optional[List[str]] = None,
    is_pinned: bool = False,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new note.
    
    Args:
        content: Note content
        title: Note title
        location: Associated location
        trip_id: Associated trip UUID
        note_type: Type ('general', 'recommendation', 'warning', 'reminder')
        tags: List of tags for categorization
        is_pinned: Pin note to top
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and note ID
    """
    user_id = user_id or _get_current_user_id()
    
    valid_types = ['general', 'recommendation', 'warning', 'reminder']
    if note_type not in valid_types:
        return {'success': False, 'error': f'Invalid note_type. Must be one of: {valid_types}'}
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_notes 
                (user_id, trip_id, location, title, content, note_type, tags, is_pinned)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, title, created_at
            """, (
                user_id, trip_id, location, title, content, note_type,
                json.dumps(tags) if tags else None, is_pinned
            ))
            
            result = cur.fetchone()
            conn.commit()
            
            return {
                'success': True,
                'id': str(result['id']),
                'title': result['title'],
                'created_at': result['created_at'].isoformat()
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create note: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def get_notes(
    trip_id: Optional[str] = None,
    location: Optional[str] = None,
    note_type: Optional[str] = None,
    pinned_only: bool = False,
    user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve user's notes with optional filters.
    
    Args:
        trip_id: Filter by trip UUID
        location: Filter by location
        note_type: Filter by type
        pinned_only: Only return pinned notes
        user_id: User ID (defaults to current user)
    
    Returns:
        List of notes
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, trip_id, location, title, content, note_type, 
                       tags, is_pinned, created_at, updated_at
                FROM user_notes
                WHERE user_id = %s
            """
            params = [user_id]
            
            if trip_id:
                query += " AND trip_id = %s"
                params.append(trip_id)
            
            if location:
                query += " AND location = %s"
                params.append(location)
            
            if note_type:
                query += " AND note_type = %s"
                params.append(note_type)
            
            if pinned_only:
                query += " AND is_pinned = TRUE"
            
            query += " ORDER BY is_pinned DESC, created_at DESC"
            
            cur.execute(query, params)
            results = cur.fetchall()
            
            return [
                {
                    'id': str(row['id']),
                    'trip_id': str(row['trip_id']) if row['trip_id'] else None,
                    'location': row['location'],
                    'title': row['title'],
                    'content': row['content'],
                    'note_type': row['note_type'],
                    'tags': row['tags'],
                    'is_pinned': row['is_pinned'],
                    'created_at': row['created_at'].isoformat(),
                    'updated_at': row['updated_at'].isoformat()
                }
                for row in results
            ]
    except Exception as e:
        logger.error(f"Failed to get notes: {e}")
        return []
    finally:
        conn.close()


def create_alert(
    location: str,
    alert_type: str,
    alert_condition: Dict,
    trip_id: Optional[str] = None,
    notification_method: str = 'in_app',
    expires_at: Optional[str] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new alert.
    
    Args:
        location: Location to monitor
        alert_type: Type ('weather', 'price', 'reminder', 'custom')
        alert_condition: Dict with conditions, e.g.,
            {'temp_above': 90} or {'weather_event': 'thunderstorm'}
        trip_id: Associated trip UUID
        notification_method: Method ('in_app', 'email', 'both')
        expires_at: Optional expiration datetime (ISO format string)
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and alert ID
    """
    user_id = user_id or _get_current_user_id()
    
    valid_types = ['weather', 'price', 'reminder', 'custom']
    if alert_type not in valid_types:
        return {'success': False, 'error': f'Invalid alert_type. Must be one of: {valid_types}'}
    
    valid_methods = ['in_app', 'email', 'both']
    if notification_method not in valid_methods:
        return {'success': False, 'error': f'Invalid notification_method. Must be one of: {valid_methods}'}
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_alerts 
                (user_id, trip_id, location, alert_type, alert_condition, 
                 notification_method, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, location, alert_type, created_at
            """, (
                user_id, trip_id, location, alert_type,
                json.dumps(alert_condition), notification_method, expires_at
            ))
            
            result = cur.fetchone()
            conn.commit()
            
            return {
                'success': True,
                'id': str(result['id']),
                'location': result['location'],
                'alert_type': result['alert_type'],
                'created_at': result['created_at'].isoformat()
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create alert: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def get_alerts(
    location: Optional[str] = None,
    alert_type: Optional[str] = None,
    active_only: bool = True,
    user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve user's alerts.
    
    Args:
        location: Filter by location
        alert_type: Filter by type
        active_only: Only return active alerts
        user_id: User ID (defaults to current user)
    
    Returns:
        List of alerts
    """
    user_id = user_id or _get_current_user_id()
    
    conn = _get_lakebase_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, trip_id, location, alert_type, alert_condition,
                       notification_method, is_active, triggered_count,
                       last_triggered_at, expires_at, created_at, updated_at
                FROM user_alerts
                WHERE user_id = %s
            """
            params = [user_id]
            
            if active_only:
                query += " AND is_active = TRUE"
                query += " AND (expires_at IS NULL OR expires_at > NOW())"
            
            if location:
                query += " AND location = %s"
                params.append(location)
            
            if alert_type:
                query += " AND alert_type = %s"
                params.append(alert_type)
            
            query += " ORDER BY created_at DESC"
            
            cur.execute(query, params)
            results = cur.fetchall()
            
            return [
                {
                    'id': str(row['id']),
                    'trip_id': str(row['trip_id']) if row['trip_id'] else None,
                    'location': row['location'],
                    'alert_type': row['alert_type'],
                    'alert_condition': row['alert_condition'],
                    'notification_method': row['notification_method'],
                    'is_active': row['is_active'],
                    'triggered_count': row['triggered_count'],
                    'last_triggered_at': row['last_triggered_at'].isoformat() if row['last_triggered_at'] else None,
                    'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                    'created_at': row['created_at'].isoformat(),
                    'updated_at': row['updated_at'].isoformat()
                }
                for row in results
            ]
    except Exception as e:
        logger.error(f"Failed to get alerts: {e}")
        return []
    finally:
        conn.close()
