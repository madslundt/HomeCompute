SELECT 'CREATE ROLE mcphub LOGIN PASSWORD ' || quote_literal(:'mcphub_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcphub')
\gexec

ALTER ROLE mcphub LOGIN PASSWORD :'mcphub_password';

SELECT 'CREATE DATABASE mcphub OWNER mcphub'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mcphub')
\gexec

ALTER DATABASE mcphub OWNER TO mcphub;
