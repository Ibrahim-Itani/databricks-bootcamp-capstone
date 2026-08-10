-- Setup script for weather_documents table
-- Run this manually in your Lakebase Postgres database before running the notebook

-- Create the weather documents table
CREATE TABLE IF NOT EXISTS weather_documents (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('alert', 'forecast')),
    headline TEXT NOT NULL,
    narrative_text TEXT NOT NULL,
    issued_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL Default now()
);

-- Create index for ticker lookups
CREATE INDEX IF NOT EXISTS idx_weather_documents
ON weather_documents (location);

-- Verify the table was created
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'weather_documents'
ORDER BY ordinal_position;