# User Actions API - Travel Recommendation System

## Overview

This system provides persistent storage for user interactions in a travel recommendation application. All data is stored in Lakebase Postgres tables with full CRUD operations.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User Actions API                         │
│           (notebooks/user_actions_api.py)                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  Favorites   │  │    Trips     │  │    Notes     │    │
│  │              │  │              │  │              │    │
│  │ • Save       │  │ • Create     │  │ • Create     │    │
│  │ • List       │  │ • Update     │  │ • Search     │    │
│  │ • Remove     │  │ • List       │  │ • Delete     │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│                                                             │
│  ┌──────────────┐  ┌──────────────────────────────┐       │
│  │   Alerts     │  │    Lakebase Postgres         │       │
│  │              │  │                              │       │
│  │ • Create     │  │  • user_favorite_locations   │       │
│  │ • Monitor    │  │  • user_trips                │       │
│  │ • Trigger    │  │  • user_notes                │       │
│  │ • Deactivate │  │  • user_alerts               │       │
│  └──────────────┘  └──────────────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Features

### 1. Favorite Locations 📍
Save and manage preferred travel destinations.

**Functions:**
- `save_favorite_location()` - Save a location with coordinates, nickname, category
- `get_favorite_locations()` - Retrieve favorites, optionally filtered by category
- `remove_favorite_location()` - Soft-delete a favorite location

**Use Cases:**
- Track places you want to visit
- Organize locations by category (home, work, vacation, family)
- Store coordinates for weather lookups
- Add personal notes about each location

### 2. Trips ✈️
Plan and track travel itineraries with dates, budget, and preferences.

**Functions:**
- `create_trip()` - Create a new trip with full details
- `get_trips()` - List trips, optionally filtered by status
- `update_trip_status()` - Change trip status (planned → active → completed)

**Features:**
- Track travelers count and budget
- Store weather preferences (ideal temp, avoid rain, etc.)
- List planned activities
- Automatic date validation
- Status workflow: planned → active → completed/cancelled

### 3. Notes 📝
Store location and trip-specific notes, recommendations, warnings.

**Functions:**
- `create_note()` - Create a note with optional trip/location association
- `get_notes()` - Search notes by trip, location, type, or pinned status
- `delete_note()` - Remove a note

**Note Types:**
- `general` - General observations
- `recommendation` - Places to visit, things to do
- `warning` - Important safety or travel warnings
- `reminder` - Action items for trips

**Features:**
- Pin important notes to the top
- Tag-based categorization
- Associate notes with specific trips or locations

### 4. Alerts 🔔
Set up automated notifications for weather conditions and custom events.

**Functions:**
- `create_alert()` - Create an alert with conditions
- `get_alerts()` - List active alerts
- `deactivate_alert()` - Turn off an alert
- `trigger_alert()` - Mark alert as triggered (used by monitoring systems)

**Alert Types:**
- `weather` - Temperature thresholds, storm warnings
- `price` - Travel cost changes
- `reminder` - Time-based reminders
- `custom` - User-defined conditions

**Features:**
- JSONB condition storage for flexible rules
- Optional expiration dates
- Track trigger count and last trigger time
- Multiple notification methods (in-app, email, both)

## Setup Instructions

### 1. Create Database Tables

```bash
# Run the SQL setup script in your Lakebase database
psql $LAKEBASE_URL -f sql/03_setup_user_actions_table.sql
```

This creates four tables:
- `user_favorite_locations`
- `user_trips`
- `user_notes`
- `user_alerts`

### 2. Configure Lakebase Connection

Ensure your Lakebase connection URL is stored in Databricks secrets:

```python
# Already configured in your workspace
scope = "database"
key = "lakebase-url"
```

### 3. Open the API Notebook

Open [user_actions_api](#notebook-469314873773545) and run all cells to load the functions.

## Example Usage

### Save a Favorite Location

```python
result = save_favorite_location(
    location_name="Paris, France",
    latitude=48.8566,
    longitude=2.3522,
    nickname="City of Light",
    category="vacation",
    notes="Amazing food, museums, and architecture"
)
print(result)
# {'success': True, 'id': '123e4567-e89b-...', 'location_name': 'Paris, France', ...}
```

### Plan a Trip

```python
from datetime import date

trip = create_trip(
    trip_name="European Summer Tour",
    destination="Paris, France",
    start_date=date(2026, 6, 15),
    end_date=date(2026, 6, 22),
    travelers_count=2,
    budget_amount=4500.00,
    weather_preferences={
        'ideal_temp_min': 65,
        'ideal_temp_max': 80,
        'avoid_rain': True
    },
    activities=['museums', 'wine tasting', 'eiffel tower'],
    metadata={'hotel': 'Le Meurice', 'flight': 'AF1234'}
)
trip_id = trip['id']
```

### Add a Note

```python
note = create_note(
    title="Best Croissants in Paris",
    content="Du Pain et des Idées - get there early, they sell out by 10am!",
    location="Paris, France",
    trip_id=trip_id,
    note_type="recommendation",
    tags=['food', 'breakfast', 'must-visit'],
    is_pinned=True
)
```

### Set Up a Weather Alert

```python
from datetime import datetime, timedelta

alert = create_alert(
    location="Paris, France",
    alert_type="weather",
    alert_condition={
        'temp_above': 85,
        'weather_events': ['thunderstorm', 'heavy rain'],
        'trigger_days_before_trip': 7
    },
    trip_id=trip_id,
    notification_method="email",
    expires_at=datetime.now() + timedelta(days=60)
)
```

### Retrieve Your Data

```python
# List all favorite locations
favorites = get_favorite_locations()

# Get vacation-category favorites only
vacation_spots = get_favorite_locations(category='vacation')

# List planned trips
planned_trips = get_trips(status='planned')

# Get all pinned notes
important_notes = get_notes(pinned_only=True)

# Get notes for a specific trip
trip_notes = get_notes(trip_id=trip_id)

# List active weather alerts
weather_alerts = get_alerts(alert_type='weather', active_only=True)
```

## Database Schema

### user_favorite_locations
```sql
id UUID PRIMARY KEY
user_id VARCHAR(255)
location_name VARCHAR(500)
latitude DECIMAL(10, 7)
longitude DECIMAL(10, 7)
nickname VARCHAR(255)
category VARCHAR(100)  -- home, work, vacation, family
notes TEXT
created_at TIMESTAMP
updated_at TIMESTAMP
is_active BOOLEAN
```

### user_trips
```sql
id UUID PRIMARY KEY
user_id VARCHAR(255)
trip_name VARCHAR(500)
destination VARCHAR(500)
start_date DATE
end_date DATE
status VARCHAR(50)  -- planned, active, completed, cancelled
travelers_count INTEGER
budget_amount DECIMAL(10, 2)
weather_preferences JSONB
activities JSONB
metadata JSONB
created_at TIMESTAMP
updated_at TIMESTAMP
```

### user_notes
```sql
id UUID PRIMARY KEY
user_id VARCHAR(255)
trip_id UUID (FK)
location VARCHAR(500)
title VARCHAR(500)
content TEXT
note_type VARCHAR(100)  -- general, recommendation, warning, reminder
tags JSONB
is_pinned BOOLEAN
created_at TIMESTAMP
updated_at TIMESTAMP
```

### user_alerts
```sql
id UUID PRIMARY KEY
user_id VARCHAR(255)
trip_id UUID (FK)
location VARCHAR(500)
alert_type VARCHAR(100)  -- weather, price, reminder, custom
alert_condition JSONB
notification_method VARCHAR(100)  -- in_app, email, both
is_active BOOLEAN
triggered_count INTEGER
last_triggered_at TIMESTAMP
created_at TIMESTAMP
updated_at TIMESTAMP
expires_at TIMESTAMP
```

## Integration with Weather Data

The User Actions API integrates seamlessly with the existing weather ingestion pipeline:

1. **Weather Ingestion** ([ingest_weather_data](#notebook-469314873773539))
   - Fetches weather from NWS API
   - Stores in `weather_documents` table

2. **User Actions** ([user_actions_api](#notebook-469314873773545))
   - Users save favorite locations
   - Create trips with weather preferences
   - Set up weather alerts

3. **Alert Monitoring** (future enhancement)
   - Check weather conditions against user alerts
   - Trigger notifications when conditions are met
   - Update `triggered_count` and `last_triggered_at`

## API Response Format

All write functions return a consistent response format:

**Success:**
```python
{
    'success': True,
    'id': 'uuid-string',
    # additional fields...
}
```

**Error:**
```python
{
    'success': False,
    'error': 'Error message description'
}
```

## User Isolation

All functions automatically scope data to the current Databricks user:
- User ID is derived from `w.current_user.me().user_name`
- All queries filter by `user_id`
- Users can only access their own data

## Performance Features

- **Indexes** on frequently queried columns (user_id, status, dates)
- **Soft deletes** (is_active flag) for faster recovery
- **JSONB columns** for flexible, schema-less data
- **Automatic timestamps** via triggers
- **Batch operations** supported via psycopg2

## Future Enhancements

1. **Alert Monitoring Service**
   - Background job to check weather conditions
   - Trigger notifications via email/in-app
   - Integration with Databricks Jobs

2. **Travel Recommendations**
   - ML model to suggest destinations based on:
     - Favorite locations
     - Past trips
     - Weather preferences
     - Budget patterns

3. **Social Features**
   - Share trips with other users
   - Collaborative trip planning
   - Note sharing and comments

4. **Analytics Dashboard**
   - Trip history visualization
   - Budget tracking over time
   - Most visited locations
   - Weather pattern analysis

## Files

- **SQL Schema**: [sql/03_setup_user_actions_table.sql](#file-469314873773544)
- **API Notebook**: [notebooks/user_actions_api](#notebook-469314873773545)
- **Weather Ingestion**: [notebooks/ingest_weather_data](#notebook-469314873773539)
- **Documentation**: [USER_ACTIONS_README.md](#file-469314873773546)

## Support

For questions or issues, refer to:
- Databricks documentation on Lakebase Postgres
- Lakebase connection setup in workspace secrets
- Weather data schema in `sql/01_setup_weather_table.sql`