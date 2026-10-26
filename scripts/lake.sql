-- DuckDB startup for ./bin/lake. Credentials come from the environment, which ./bin/lake loads from .env.
-- Polaris hands DuckDB short-lived storage credentials per table, so no AWS keys are needed.
.bail on
.output /dev/null
INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;
CREATE OR REPLACE SECRET polaris (
  TYPE iceberg,
  CLIENT_ID getenv('POLARIS_CLIENT_ID'),
  CLIENT_SECRET getenv('POLARIS_CLIENT_SECRET'),
  OAUTH2_SERVER_URI rtrim(getenv('POLARIS_ENDPOINT'), '/') || '/v1/oauth/tokens',
  OAUTH2_SCOPE 'PRINCIPAL_ROLE:ALL'
);
.output
