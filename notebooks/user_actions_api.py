# Databricks notebook source
# DBTITLE 1,User Actions API
# MAGIC %md
# MAGIC # User Actions API
# MAGIC
# MAGIC Provides write/action functions to persist user interactions to Lakebase:
# MAGIC * **Favorite Locations** - Save and manage preferred destinations
# MAGIC * **Trips** - Log and track travel plans
# MAGIC * **Notes** - Store location and trip-related notes
# MAGIC * **Alerts** - Set up weather and custom notifications
# MAGIC
# MAGIC ## Prerequisites
# MAGIC
# MAGIC 1. Run `sql/03_setup_user_actions_table.sql` to create the user actions tables
# MAGIC 2. Ensure Lakebase connection is configured in secrets
# MAGIC 3. Current user ID is derived from Databricks workspace context

# COMMAND ----------

# DBTITLE 1,Install packages
# MAGIC %pip install -q 'databricks-sdk>=0.118.0' psycopg2-binary

# COMMAND ----------

# DBTITLE 1,Restart kernel
dbutils.library.restartPython()

# COMMAND ----------

# DBTITLE 1,Lakebase connection setup
import base64
import json
from urllib.parse import urlparse
from databricks.sdk import WorkspaceClient
from typing import Optional, Dict, Any, List
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
import uuid

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

# Get current user ID from workspace
current_user = w.current_user.me()
USER_ID = current_user.user_name

print(f"Connection details:")
print(f"  Host: {db_host}:{db_port}")
print(f"  Database: {db_name}")
print(f"  User: {db_user}")
print(f"\nCurrent User ID: {USER_ID}")

def get_connection():
    """Get a psycopg2 connection to Lakebase."""
    return psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode='require'
    )

# COMMAND ----------

# DBTITLE 1,Favorite Locations Functions
# MAGIC %md
# MAGIC ## Favorite Locations
# MAGIC
# MAGIC Save and manage user's favorite travel destinations.

# COMMAND ----------

# DBTITLE 1,Favorite locations write functions
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
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    finally:
        conn.close()


def remove_favorite_location(
    location_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Remove a location from favorites (soft delete).
    
    Args:
        location_id: UUID of the location to remove
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status
    """
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_favorite_locations
                SET is_active = FALSE, updated_at = NOW()
                WHERE id = %s AND user_id = %s
            """, (location_id, user_id))
            
            conn.commit()
            
            if cur.rowcount > 0:
                return {'success': True, 'message': 'Location removed from favorites'}
            else:
                return {'success': False, 'error': 'Location not found'}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


# Test the functions
print("Favorite Locations API loaded successfully!")
print("\nAvailable functions:")
print("  - save_favorite_location(location_name, latitude, longitude, nickname, category, notes)")
print("  - get_favorite_locations(category=None)")
print("  - remove_favorite_location(location_id)")

# COMMAND ----------

# DBTITLE 1,Trips Functions
# MAGIC %md
# MAGIC ## Trips
# MAGIC
# MAGIC Log and track user's travel plans with dates, budget, and preferences.

# COMMAND ----------

# DBTITLE 1,Trips write functions
def create_trip(
    trip_name: str,
    destination: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
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
        start_date: Trip start date
        end_date: Trip end date
        travelers_count: Number of travelers
        budget_amount: Budget in dollars
        weather_preferences: Dict with preferences like {'ideal_temp': 75, 'avoid_rain': True}
        activities: List of planned activities
        metadata: Additional trip metadata
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and trip ID
    """
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    finally:
        conn.close()


def update_trip_status(
    trip_id: str,
    status: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update trip status.
    
    Args:
        trip_id: UUID of the trip
        status: New status ('planned', 'active', 'completed', 'cancelled')
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status
    """
    user_id = user_id or USER_ID
    
    valid_statuses = ['planned', 'active', 'completed', 'cancelled']
    if status not in valid_statuses:
        return {'success': False, 'error': f'Invalid status. Must be one of: {valid_statuses}'}
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_trips
                SET status = %s, updated_at = NOW()
                WHERE id = %s AND user_id = %s
            """, (status, trip_id, user_id))
            
            conn.commit()
            
            if cur.rowcount > 0:
                return {'success': True, 'message': f'Trip status updated to {status}'}
            else:
                return {'success': False, 'error': 'Trip not found'}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


print("Trips API loaded successfully!")
print("\nAvailable functions:")
print("  - create_trip(trip_name, destination, start_date, end_date, ...)")
print("  - get_trips(status=None)")
print("  - update_trip_status(trip_id, status)")

# COMMAND ----------

# DBTITLE 1,Notes Functions
# MAGIC %md
# MAGIC ## Notes
# MAGIC
# MAGIC Store location-specific and trip-related notes.

# COMMAND ----------

# DBTITLE 1,Notes write functions
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
    user_id = user_id or USER_ID
    
    valid_types = ['general', 'recommendation', 'warning', 'reminder']
    if note_type not in valid_types:
        return {'success': False, 'error': f'Invalid note_type. Must be one of: {valid_types}'}
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    finally:
        conn.close()


def delete_note(
    note_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Delete a note.
    
    Args:
        note_id: UUID of the note
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status
    """
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM user_notes
                WHERE id = %s AND user_id = %s
            """, (note_id, user_id))
            
            conn.commit()
            
            if cur.rowcount > 0:
                return {'success': True, 'message': 'Note deleted'}
            else:
                return {'success': False, 'error': 'Note not found'}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


print("Notes API loaded successfully!")
print("\nAvailable functions:")
print("  - create_note(content, title, location, trip_id, note_type, tags, is_pinned)")
print("  - get_notes(trip_id, location, note_type, pinned_only)")
print("  - delete_note(note_id)")

# COMMAND ----------

# DBTITLE 1,Alerts Functions
# MAGIC %md
# MAGIC ## Alerts
# MAGIC
# MAGIC Set up weather alerts and custom notifications for locations and trips.

# COMMAND ----------

# DBTITLE 1,Alerts write functions
def create_alert(
    location: str,
    alert_type: str,
    alert_condition: Dict,
    trip_id: Optional[str] = None,
    notification_method: str = 'in_app',
    expires_at: Optional[datetime] = None,
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
        expires_at: Optional expiration datetime
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status and alert ID
    """
    user_id = user_id or USER_ID
    
    valid_types = ['weather', 'price', 'reminder', 'custom']
    if alert_type not in valid_types:
        return {'success': False, 'error': f'Invalid alert_type. Must be one of: {valid_types}'}
    
    valid_methods = ['in_app', 'email', 'both']
    if notification_method not in valid_methods:
        return {'success': False, 'error': f'Invalid notification_method. Must be one of: {valid_methods}'}
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
    finally:
        conn.close()


def deactivate_alert(
    alert_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Deactivate an alert.
    
    Args:
        alert_id: UUID of the alert
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status
    """
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_alerts
                SET is_active = FALSE, updated_at = NOW()
                WHERE id = %s AND user_id = %s
            """, (alert_id, user_id))
            
            conn.commit()
            
            if cur.rowcount > 0:
                return {'success': True, 'message': 'Alert deactivated'}
            else:
                return {'success': False, 'error': 'Alert not found'}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def trigger_alert(
    alert_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Mark an alert as triggered (used by monitoring systems).
    
    Args:
        alert_id: UUID of the alert
        user_id: User ID (defaults to current user)
    
    Returns:
        Dict with success status
    """
    user_id = user_id or USER_ID
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_alerts
                SET triggered_count = triggered_count + 1,
                    last_triggered_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s AND is_active = TRUE
            """, (alert_id, user_id))
            
            conn.commit()
            
            if cur.rowcount > 0:
                return {'success': True, 'message': 'Alert triggered'}
            else:
                return {'success': False, 'error': 'Alert not found or inactive'}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


print("Alerts API loaded successfully!")
print("\nAvailable functions:")
print("  - create_alert(location, alert_type, alert_condition, trip_id, notification_method, expires_at)")
print("  - get_alerts(location, alert_type, active_only)")
print("  - deactivate_alert(alert_id)")
print("  - trigger_alert(alert_id)")

# COMMAND ----------

# DBTITLE 1,Example Usage
# MAGIC %md
# MAGIC ## Example Usage
# MAGIC
# MAGIC Demonstrates how to use the write/action functions.

# COMMAND ----------

# DBTITLE 1,Example: Save favorite location
# Example: Save a favorite vacation spot
result = save_favorite_location(
    location_name="Maui, Hawaii",
    latitude=20.7984,
    longitude=-156.3319,
    nickname="Dream Vacation Spot",
    category="vacation",
    notes="Best beaches, great weather year-round"
)

print(json.dumps(result, indent=2))

# COMMAND ----------

# DBTITLE 1,Example: Create a trip
from datetime import date

# Example: Plan a summer trip
trip_result = create_trip(
    trip_name="Summer Beach Vacation",
    destination="Maui, Hawaii",
    start_date=date(2026, 7, 15),
    end_date=date(2026, 7, 22),
    travelers_count=2,
    budget_amount=3500.00,
    weather_preferences={
        'ideal_temp_min': 75,
        'ideal_temp_max': 85,
        'avoid_rain': True
    },
    activities=['snorkeling', 'hiking', 'surfing'],
    metadata={'accommodation': 'resort', 'rental_car': True}
)

print(json.dumps(trip_result, indent=2))

# Store trip_id for later examples
if trip_result['success']:
    example_trip_id = trip_result['id']
    print(f"\nSaved trip ID: {example_trip_id}")

# COMMAND ----------

# DBTITLE 1,Example: Create a note
# Example: Add a recommendation note
note_result = create_note(
    title="Best Snorkeling Spot",
    content="Molokini Crater is amazing for snorkeling - crystal clear water, tons of fish. Book early morning tour!",
    location="Maui, Hawaii",
    trip_id=example_trip_id if 'example_trip_id' in locals() else None,
    note_type="recommendation",
    tags=['snorkeling', 'must-do', 'marine-life'],
    is_pinned=True
)

print(json.dumps(note_result, indent=2))

# COMMAND ----------

# DBTITLE 1,Example: Create a weather alert
from datetime import datetime, timedelta

# Example: Set up a weather alert for the trip
alert_result = create_alert(
    location="Maui, Hawaii",
    alert_type="weather",
    alert_condition={
        'temp_above': 90,
        'weather_events': ['thunderstorm', 'tropical storm'],
        'trigger_days_before_trip': 7
    },
    trip_id=example_trip_id if 'example_trip_id' in locals() else None,
    notification_method="both",
    expires_at=datetime.now() + timedelta(days=90)
)

print(json.dumps(alert_result, indent=2))

# COMMAND ----------

# DBTITLE 1,Example: Retrieve data
# Retrieve all saved data
print("=" * 80)
print("FAVORITE LOCATIONS")
print("=" * 80)
favorites = get_favorite_locations()
for fav in favorites:
    print(f"\n📍 {fav['nickname'] or fav['location_name']}")
    print(f"   Location: {fav['location_name']}")
    print(f"   Category: {fav['category']}")
    if fav['notes']:
        print(f"   Notes: {fav['notes']}")

print("\n" + "=" * 80)
print("TRIPS")
print("=" * 80)
trips = get_trips()
for trip in trips:
    print(f"\n✈️  {trip['trip_name']}")
    print(f"   Destination: {trip['destination']}")
    print(f"   Status: {trip['status']}")
    if trip['start_date']:
        print(f"   Dates: {trip['start_date']} to {trip['end_date']}")
    if trip['budget_amount']:
        print(f"   Budget: ${trip['budget_amount']:.2f}")

print("\n" + "=" * 80)
print("NOTES")
print("=" * 80)
notes = get_notes()
for note in notes:
    pin_emoji = "📌" if note['is_pinned'] else "📝"
    print(f"\n{pin_emoji} {note['title'] or 'Untitled Note'}")
    print(f"   Type: {note['note_type']}")
    if note['location']:
        print(f"   Location: {note['location']}")
    print(f"   Content: {note['content'][:100]}..." if len(note['content']) > 100 else f"   Content: {note['content']}")

print("\n" + "=" * 80)
print("ALERTS")
print("=" * 80)
alerts = get_alerts()
for alert in alerts:
    print(f"\n🔔 {alert['alert_type'].upper()} Alert")
    print(f"   Location: {alert['location']}")
    print(f"   Condition: {json.dumps(alert['alert_condition'])}")
    print(f"   Method: {alert['notification_method']}")
    print(f"   Active: {alert['is_active']}")
    if alert['triggered_count'] > 0:
        print(f"   Triggered: {alert['triggered_count']} times (last: {alert['last_triggered_at']})")

# COMMAND ----------

