-- MomVoice AI database schema (PostgreSQL)
-- Run this once against your Cloud SQL instance to create the tables.
--
-- ALREADY RAN THIS BEFORE? Since your tables already exist, the
-- CREATE TABLE statements below will be skipped (that's what
-- "IF NOT EXISTS" does). Instead, just run this one line to add the
-- new gender column to your existing users table:
--
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS baby_gender TEXT;

CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    baby_name TEXT,
    baby_dob DATE,
    baby_gender TEXT,                 -- e.g. "girl", "boy", "prefer not to say"
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entries (
    entry_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(user_id),
    category TEXT NOT NULL,          -- feeding / sleep / diaper / milestone
    details TEXT,                     -- e.g. "6oz", "wet", free text
    logged_at TIMESTAMP,              -- when the event happened
    created_at TIMESTAMP DEFAULT NOW(),
    raw_message TEXT                  -- original parent message, for debugging
);

CREATE TABLE IF NOT EXISTS guidance_queries (
    query_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(user_id),
    question TEXT,
    answer TEXT,
    asked_at TIMESTAMP DEFAULT NOW()
);
