# Run the project

Use Python (`3.12`), a running PostgreSQL server, and an OpenAI API key with access to the selected model.

Run commands from the repository root unless indicated otherwise.

## 1. Install Python packages

Optionally, create and activate a virtual environment:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
```

Install the dependencies in your chosen Python environment:

```sh
python -m pip install -r requirements.txt
```

`psycopg2` may require PostgreSQL development libraries and `pg_config` on your PATH. 

## 2. Create an example database

[AdventureWorks](https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works) databse provides employees, products, purchasing, and sales data. 

Its installer and CSV files are included in `data/adventure-works/`.

Connect as a PostgreSQL administrator (`psql postgres`, or `psql -U postgres -d postgres`), then execute:

```sql
CREATE USER adventureworksuser WITH PASSWORD 'adventureworksuser_password';
CREATE DATABASE adventureworks OWNER adventureworksuser;
```

For a **new, empty database**, import the sample from the installer directory so its relative CSV paths resolve:

```sh
(
  cd data/adventure-works/installer
  psql -h localhost -U adventureworksuser -d adventureworks -v ON_ERROR_STOP=1 -f install.sql
)
```

Enter the password when prompted. PostgreSQL must have the `uuid-ossp` and `tablefunc` extensions available since the installer creates them. Skip this section if your database already exists.

## 3. Set environment variables

If you do not already have a `.env`, copy `.env.default` to `.env`. Edit it with your own API key and database credentials:

```dotenv
OPENAI_API_KEY=YOUR_OWN_OPENAI_API_KEY

POSTGRES_DB_HOST=localhost
POSTGRES_DB_PORT=5432

POSTGRES_DB_USER=adventureworksuser
POSTGRES_DB_PASSWORD=adventureworksuser_password
POSTGRES_DB_NAME=adventureworks
```

The application does not automatically load `.env`. Host and port default to `localhost` and `5432`, while other values are required.

Export the values in the terminal used for subsequent commands:

```sh
set -a
source .env
set +a
```

## 4. Generate database documentation

```sh
python -m documentation.auto_doc.generate_database_schema
```

This reads table definitions, keys, and row counts into `documentation/databases/<POSTGRES_DB_NAME>/schema.yaml`, which the agents use to generate SQL. 

The database user needs access to the schemas and `SELECT` on their tables. Regenerate after changing the database structure.

## 5. Launch Streamlit and ask questions

Generated Python checks run locally: the separate process enforces a timeout but is not a security sandbox, so use a local sample database when trying the project.

```sh
PYTHONPATH=. streamlit run interface/main.py --server.port 8501
```

Open [localhost:8501](http://localhost:8501). Start with the default model (`gpt-4.1-mini`) and one check. 

Enter a question and click **Process Request**, for example:

- _How many employees are there?_
- _How many employees per department?_
- _How many products are there in each product category?_
- _Which five vendors have the most purchase orders?_

Inspect the SQL, result table, validation checks, and refinement history. 

The sidebar controls SQL repair attempts and additional refinement steps. Set refinement steps to `0` for a single pass. 

Queries use read-only transactions, a 30-second statement timeout, and a 1,000-row result limit. Prefer aggregates or explicitly limited results for large tables.

## 6. Run tests

With the same environment variables exported and PostgreSQL available:

```sh
python -m pytest tests/
```
