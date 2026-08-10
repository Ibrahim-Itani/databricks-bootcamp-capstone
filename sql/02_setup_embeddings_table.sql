CREATE TABLE IF NOT EXISTS weather_documents_embeddings (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    headline TEXT NOT NULL,
    published_utc TIMESTAMPTZ,
    embedding VECTOR(384) NOT NULL,
    model_name TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_weather_documents_embeddings_embedding
ON weather_documents_embeddings
USING hnsw (embedding vector_cosine_ops);

-- Verify the table was created
SELECT 
    table_name,
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_name = 'weather_documents_embeddings'
ORDER BY ordinal_position;