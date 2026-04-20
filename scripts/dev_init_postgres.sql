-- Runs once on first boot of the dev postgres container (via
-- /docker-entrypoint-initdb.d). Creates an extra DB used exclusively by
-- pytest so unit tests never touch the dev working DB.
CREATE DATABASE leavebot_test OWNER leavebot ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE leavebot_test TO leavebot;
