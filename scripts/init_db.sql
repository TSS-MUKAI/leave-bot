-- Create a dedicated role and database for leave-bot on the shared postgres instance.
-- Run as the mmuser (or any superuser) against the mattermost database, e.g.:
--   docker exec -i docker-postgres-1 psql -U mmuser -d mattermost < scripts/init_db.sql
--
-- Change the password before running in production.

CREATE ROLE leavebot WITH LOGIN PASSWORD 'leavebot';
CREATE DATABASE leavebot OWNER leavebot ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE leavebot TO leavebot;
