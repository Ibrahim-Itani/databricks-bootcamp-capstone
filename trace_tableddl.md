CREATE TABLE production.mcp_tool_traces (
    trace_id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    user_email TEXT NOT NULL,
    parameters JSONB NOT NULL,
    result JSONB,
    duration_ms FLOAT NOT NULL,
    error TEXT,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX idx_traces_session ON production.mcp_tool_traces(session_id);
CREATE INDEX idx_traces_tool ON production.mcp_tool_traces(tool_name);
CREATE INDEX idx_traces_user ON production.mcp_tool_traces(user_email);
CREATE INDEX idx_traces_timestamp ON production.mcp_tool_traces(timestamp DESC);
