-- User Actions Table Setup for Lakebase Postgres
-- This table stores all user interactions: favorite locations, trips, notes, and alerts

-- Enable UUID extension for generating unique IDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Drop existing tables if they exist (for development)
DROP TABLE IF EXISTS user_alerts CASCADE;
DROP TABLE IF EXISTS user_trips CASCADE;
DROP TABLE IF EXISTS user_notes CASCADE;
DROP TABLE IF EXISTS user_favorite_locations CASCADE;

-- =============================================================================
-- FAVORITE LOCATIONS TABLE
-- =============================================================================
CREATE TABLE user_favorite_locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    location_name VARCHAR(500) NOT NULL,
    latitude DECIMAL(10, 7),
    longitude DECIMAL(10, 7),
    nickname VARCHAR(255),
    category VARCHAR(100), -- e.g., 'home', 'work', 'vacation', 'family'
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Index for efficient user queries
    CONSTRAINT unique_user_location UNIQUE (user_id, location_name)
);

CREATE INDEX idx_favorite_locations_user ON user_favorite_locations(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_favorite_locations_category ON user_favorite_locations(user_id, category) WHERE is_active = TRUE;

-- =============================================================================
-- TRIPS TABLE
-- =============================================================================
CREATE TABLE user_trips (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    trip_name VARCHAR(500) NOT NULL,
    destination VARCHAR(500) NOT NULL,
    start_date DATE,
    end_date DATE,
    status VARCHAR(50) DEFAULT 'planned', -- 'planned', 'active', 'completed', 'cancelled'
    travelers_count INTEGER,
    budget_amount DECIMAL(10, 2),
    weather_preferences JSONB, -- Store preferences like ideal_temp, avoid_rain, etc.
    activities JSONB, -- Array of planned activities
    metadata JSONB, -- Store additional trip details
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    -- Validation
    CONSTRAINT valid_date_range CHECK (end_date >= start_date),
    CONSTRAINT valid_status CHECK (status IN ('planned', 'active', 'completed', 'cancelled'))
);

CREATE INDEX idx_trips_user ON user_trips(user_id);
CREATE INDEX idx_trips_status ON user_trips(user_id, status);
CREATE INDEX idx_trips_dates ON user_trips(start_date, end_date);

-- =============================================================================
-- NOTES TABLE
-- =============================================================================
CREATE TABLE user_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    trip_id UUID REFERENCES user_trips(id) ON DELETE CASCADE,
    location VARCHAR(500),
    title VARCHAR(500),
    content TEXT NOT NULL,
    note_type VARCHAR(100) DEFAULT 'general', -- 'general', 'recommendation', 'warning', 'reminder'
    tags JSONB, -- Array of tags for categorization
    is_pinned BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    CONSTRAINT valid_note_type CHECK (note_type IN ('general', 'recommendation', 'warning', 'reminder'))
);

CREATE INDEX idx_notes_user ON user_notes(user_id);
CREATE INDEX idx_notes_trip ON user_notes(trip_id) WHERE trip_id IS NOT NULL;
CREATE INDEX idx_notes_pinned ON user_notes(user_id, is_pinned) WHERE is_pinned = TRUE;
CREATE INDEX idx_notes_type ON user_notes(user_id, note_type);

-- =============================================================================
-- ALERTS TABLE
-- =============================================================================
CREATE TABLE user_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(255) NOT NULL,
    trip_id UUID REFERENCES user_trips(id) ON DELETE CASCADE,
    location VARCHAR(500) NOT NULL,
    alert_type VARCHAR(100) NOT NULL, -- 'weather', 'price', 'reminder', 'custom'
    alert_condition JSONB, -- Store conditions like temp threshold, weather events, etc.
    notification_method VARCHAR(100) DEFAULT 'in_app', -- 'in_app', 'email', 'both'
    is_active BOOLEAN DEFAULT TRUE,
    triggered_count INTEGER DEFAULT 0,
    last_triggered_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP, -- Optional expiration for time-based alerts
    
    CONSTRAINT valid_alert_type CHECK (alert_type IN ('weather', 'price', 'reminder', 'custom')),
    CONSTRAINT valid_notification_method CHECK (notification_method IN ('in_app', 'email', 'both'))
);

CREATE INDEX idx_alerts_user ON user_alerts(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_alerts_trip ON user_alerts(trip_id) WHERE trip_id IS NOT NULL AND is_active = TRUE;
CREATE INDEX idx_alerts_location ON user_alerts(location) WHERE is_active = TRUE;
CREATE INDEX idx_alerts_expiration ON user_alerts(expires_at) WHERE expires_at IS NOT NULL AND is_active = TRUE;

-- =============================================================================
-- UPDATE TIMESTAMP TRIGGERS
-- =============================================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$ LANGUAGE plpgsql;

-- Attach triggers to all tables
CREATE TRIGGER update_favorite_locations_updated_at
    BEFORE UPDATE ON user_favorite_locations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_trips_updated_at
    BEFORE UPDATE ON user_trips
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_notes_updated_at
    BEFORE UPDATE ON user_notes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_alerts_updated_at
    BEFORE UPDATE ON user_alerts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- SAMPLE QUERIES FOR VERIFICATION
-- =============================================================================

-- Check table structures
-- SELECT table_name, column_name, data_type 
-- FROM information_schema.columns 
-- WHERE table_name IN ('user_favorite_locations', 'user_trips', 'user_notes', 'user_alerts')
-- ORDER BY table_name, ordinal_position;

-- Count rows in each table
-- SELECT 
--     (SELECT COUNT(*) FROM user_favorite_locations) as favorite_locations_count,
--     (SELECT COUNT(*) FROM user_trips) as trips_count,
--     (SELECT COUNT(*) FROM user_notes) as notes_count,
--     (SELECT COUNT(*) FROM user_alerts) as alerts_count;