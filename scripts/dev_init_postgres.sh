#!/bin/sh
# Runs on first boot of the dev postgres container. Creates an extra DB used
# exclusively by pytest so unit tests never touch the dev working DB.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE leavebot_test OWNER ${POSTGRES_USER} ENCODING 'UTF8';
    GRANT ALL PRIVILEGES ON DATABASE leavebot_test TO ${POSTGRES_USER};
EOSQL
