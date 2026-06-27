# DarkAtlas — Asset Management System (AI Applications Track)

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Project Structure](#2-project-structure)
3. [Installation & Setup](#3-installation--setup)
   - [Prerequisites](#prerequisites)
   - [Environment Variables](#environment-variables)
   - [Running with Docker](#running-with-docker)
   - [Accessing the Database](#accessing-the-database)
4. [AI Capabilities](#4-ai-capabilities)
   - [Natural-Language Asset Query](#41-natural-language-asset-query)
   - [Risk Scoring & Summarization](#42-risk-scoring--summarization)
   - [Automated Enrichment & Categorization](#43-automated-enrichment--categorization)
   - [Natural-Language Report Generation](#44-natural-language-report-generation)
5. [Data Modeling](#5-data-modeling)
   - [Asset Model](#51-asset-model)
   - [Relationships Graph](#52-relationships-graph)
   - [Database Schema](#53-database-schema)
6. [Design Decisions & Assumptions](#6-design-decisions--assumptions)
7. [Future Improvements](#7-future-improvements)

---

## 1. Project Overview

Security teams inherently struggle to track their external attack surface across hundreds of domains, IPs, certificates, and services. DarkAtlas solves this by creating a structured Asset Management module coupled with a secure RAG (Retrieval-Augmented Generation) analysis pipeline. 

This module serves as the heart of the platform and acts as the system of record. It is designed to ingest discovered assets, remove duplicates, track each asset's lifecycle and relationships, and expose everything for advanced querying, analysis, and reporting. 


---

## 2. Project Structure

```
D:\Asset_Management_System
├── .env.example          # Template for environment variables (API keys, DB credentials)
├── .gitignore            # Files and folders to ignore in Git (e.g., .venv, secrets)
├── .python-version       # Specifies the exact Python version used for the project
├── pyproject.toml        # Project configuration and dependency management (using uv)
├── README.md             # Project documentation and setup guide
├── uv.lock               # Locked dependency versions for reproducible environments
├── .venv/                # Isolated local virtual environment directory
├── app/
│   ├── __init__.py       # Initializes the app package
│   ├── database.py       # SQLAlchemy engine and session configuration
│   ├── main.py           # FastAPI application entry point and database initialization
│   ├── models.py         # SQLAlchemy database models (assets, organizations, relationships, batches)
│   ├── schemas.py        # Pydantic models for data validation and request/response payloads
│   ├── routers/
│   │   ├── __init__.py   # Initializes the routers package
│   │   ├── ai_analyze.py # Endpoints for natural-language query, risk scoring, and report generation
│   │   └── ingest.py     # Endpoints for handling bulk asset ingestion pipelines
│   └── services/
│       ├── __init__.py   # Initializes the services package
│       ├── ai_service.py # Core LangChain logic for RAG and unstructured text analysis
│       └── ai_enrichment.py # Background task management for automated asset categorization
└── migrations/
    └── 001_add_subdomain_to_ip_address_relationship.sql  # SQL migration for relationship constraints
```

---

## 3. Installation & Setup

### Prerequisites

Make sure you have the following installed on your local development machine:

- **Docker** (v20.10+ recommended)
- **Docker Compose**
- *Optional:* A database GUI client like **pgAdmin 4** or **DBeaver** to inspect data live.

### Environment Variables

The system isolates sensitive keys and configurations outside of the repository using an environment file.

1. Duplicate the `.env.example` file and rename it to `.env`:

   ```bash
   cp .env.example .env
   ```

2. Open the `.env` file and append your external service credentials:

   ```env
   COHERE_API_KEY=your_actual_cohere_api_key_here
   DATABASE_URL=postgresql://buguard_user:buguard_password@db:5432/buguard_db
   ```

> **Note:** The system supports multiple LLM providers. While Cohere is utilized out of the box, you can seamlessly configure OpenAI, Anthropic, or Gemini keys depending on your deployment preferences.

### Running with Docker

To compile the FastAPI application and orchestrate it with a dedicated PostgreSQL database, execute the following command in the project root directory:

```bash
docker-compose up -d --build
```

This single command handles container assembly, maps inner host ports, ensures the database container undergoes health checks before launching the web server, and triggers SQLAlchemy's automatic initialization scripts.

* **Interactive Swagger UI (Recommended):** Visit [http://localhost:8000/docs](http://localhost:8000/docs) directly in your browser to explore, validate schemas, and execute live API endpoints visually.
* **Postman Client:** You can import the endpoints into Postman and point your requests to `http://localhost:8000` to simulate the asset ingestion and AI pipelines.

### Accessing the Database

If you wish to query the database instance directly via pgAdmin or DBeaver:

1. Turn off any local PostgreSQL server instance running natively on your machine to free up port `5432`.

2. Register a new connection using these properties:

   | Property | Value |
   |---|---|
   | Host | `localhost` |
   | Port | `5432` |
   | Database | `buguard_db` |
   | Username | `buguard_user` |
   | Password | `buguard_password` |

---

## 4. AI Capabilities

> All four capabilities are powered by a LangChain layer using structured output, grounded prompts, and hallucination guards. The LLM never invents assets — every answer is backed by data retrieved from PostgreSQL.

### 4.1 Natural-Language Asset Query

**Endpoint:** `POST /analyze/query`

Accepts plain-text questions regarding infrastructure layout and safely maps them to programmatic filters to read database objects without exposing raw SQL injection vectors.


![Alt text](./images/Capability1.PNG)

**How it works (Workflow):**
* User Query: The user submits a question in natural language (e.g., "show me all expired certificates on production subdomains").

* Information Extraction: The LangChain layer processes the text, acts as an intelligence gatekeeper, and extracts the core parameters into a clean, structured JSON format.

* Safe Database Lookup: Instead of letting the AI write raw SQL directly, the system passes this structured JSON to the SQLAlchemy ORM, which securely builds and executes the matching database query.

* Result Delivery: The database returns the verified records, and the system delivers the clean matches back to the user interface.

**Example prompt:**

```
"show me all expired certificates on production subdomains"
```

**Example output:**

```json
{
    "user_query": "show me all expired certificates on production subdomains",
    "interpreted_filters": {
        "reasoning": "The user is asking for expired certificates on production subdomains. This translates to: certificates that have already expired (is_expired=True), specifically for subdomain assets (asset_type='subdomain'), and in the production environment (environment='prod'). No timeframe is needed since we're looking for already expired certificates rather than expiring ones.",
        "is_out_of_scope": false,
        "asset_type": "subdomain",
        "environment": "prod",
        "is_expired": true,
        "logical_operator": "AND"
    },
    "count": 0,
    "results": []
}
```

---

### 4.2 Risk Scoring & Summarization

**Endpoint:** `POST /analyze/risk`

Evaluates assets against security heuristics (such as internal ports exposed externally, old technology stacks, or untrusted certs) to synthesize critical severity weights and textual justifications.

![Alt text](./images/Capability2.PNG)

**How it works (Workflow):**
* User Request: The user asks for a security evaluation of an asset or a group (e.g., "assess the risk of all active services").

* Data Retrieval (ORM): The system queries the PostgreSQL database via SQLAlchemy to fetch the complete current state and metadata of the targeted assets.

* Intelligent Analysis (LLM): The fetched asset details are passed into the LangChain layer. The LLM processes the live data against security rules, analyzing vectors like expired certificates, exposed sensitive ports (like SSH or database ports), and end-of-life technologies.

* Synthesized Output: The LLM calculates an overall numerical risk score, assigns a severity tier (e.g., High, Critical), creates a concise textual summary, and lists immediate remediation steps to return to the user.

**Example prompt:**

```
"assess the risk of all active services"
```

**Example output:**

```json
{
    "user_query": "assess the risk of all active services",
    "interpreted_filters": {
        "reasoning": "The user is asking to assess risk for 'all active services'. This translates to filtering for services (asset_type) that have an active lifecycle status. No other specific criteria like environment, tags, or timeframes were mentioned, so I apply only the essential filters. Since they said 'all', I don't set a limit to ensure comprehensive results.",
        "is_out_of_scope": false,
        "asset_type": "service",
        "status": "active",
        "logical_operator": "AND"
    },
    "analyzed_assets_count": 10,
    "risk_assessment": {
        "overall_risk_level": "Critical",
        "overall_risk_score": 95,
        "summary": "Analyzed 10 services across dev, staging, and production environments. Critical risks identified: admin/database ports (5432, 3389) exposed on production assets. Additional high-risk exposures of similar ports exist on non-production assets. Immediate action required to firewall production admin/DB ports and enforce network segmentation.",
        "findings": [
            {
                "asset_id": "1cd5c8e6-d8a3-5dd7-9a19-63956d143c3b",
                "asset_type": "service",
                "asset_value": "5432/tcp",
                "risk_level": "Critical",
                "reason": "Admin/DB port 5432 (PostgreSQL) exposed on production asset"
            },
            {
                "asset_id": "d79c9dd3-344a-55ef-ad76-6d0562eaed74",
                "asset_type": "service",
                "asset_value": "3389/udp",
                "risk_level": "Critical",
                "reason": "Admin/DB port 3389 (RDP) exposed on production asset"
            },
            {
                "asset_id": "4f149799-cee6-5067-b89f-37613a0c0681",
                "asset_type": "service",
                "asset_value": "3389/tcp",
                "risk_level": "High",
                "reason": "Admin/DB port 3389 (RDP) exposed on non-production asset"
            },
            {
                "asset_id": "00f57d18-c101-5a48-8f09-034d9b625ab8",
                "asset_type": "service",
                "asset_value": "3306/tcp",
                "risk_level": "High",
                "reason": "Database port 3306 (MySQL) exposed on non-production asset"
            },
            {
                "asset_id": "16d0eb23-2487-5264-877b-d727014a0c8c",
                "asset_type": "service",
                "asset_value": "22/tcp",
                "risk_level": "High",
                "reason": "SSH port 22 exposed on non-production asset"
            }
        ]
    }
}
```

---

### 4.3 Automated Enrichment & Categorization

**Endpoint:** `POST /ingest/bulk` *(triggers BackgroundTasks)*

Upon bulk ingestion, the asset is saved instantly to allow unblocked standard workflows. Concurrently, an asynchronous background pipeline calls the AI engine to automatically categorize and extract technical attributes into the asset's JSON metadata column.

![Alt text](./images/Capability3.PNG)

**How it works (Workflow):**

* Asset Ingestion: A user or an external scanner submits a batch of raw assets (e.g., a list of domains or IPs) to the bulk ingestion endpoint.  

* Immediate Database Save: To maximize performance and keep the API non-blocking, the system instantly generates unique asset IDs, saves the raw data to PostgreSQL using the SQLAlchemy ORM, and returns a 201 Created status to the user.

* Asynchronous Background Task Trigger: Concurrently, the application spawns an independent Background Task. This task grabs the raw asset values and passes them to the LangChain layer.  AI Categorization (LLM): The LLM evaluates the raw asset (e.g., qa-k8s-cluster.test.net) and intelligently deduces its environment (e.g., QA / Testing), infrastructure type (e.g., Kubernetes Node), and relevant security tags.  

* Progressive Metadata Update: The background worker takes these AI-extracted fields and progressively updates the asset's metadata JSON column in the database. 

**Example input asset:**

```json
{
  "type": "subdomain",
  "value": "qa-k8s-cluster.test.net",
  "organization_id": "00000000-0000-0000-0000-000000000000"
}
```

**Example enriched output** *(reflected in DB after background task executes):*

```json
{
  "id": "c1aef591-ba91-4c6e-8a03-7729b8c919a3",
  "type": "subdomain",
  "value": "qa-k8s-cluster.test.net",
  "status": "active",
  "metadata": {
    "environment": "QA / Testing",
    "infrastructure_type": "Kubernetes Cluster Node",
    "automated_tags": ["k8s", "internal-testing", "unauthenticated-entrypoint"]
  }
}
```

---

### 4.4 Natural-Language Report Generation

**Endpoint:** `POST /analyze/report`

**Download as `.md` file:** `POST /analyze/report/download`

Generates structured markdown security briefs detailing the overall risk surface, recent ingest historical snapshots, and logical remediation actions.

![Alt text](./images/Capability4.PNG)

**How it works (Workflow):**
* User Request: The user asks for a comprehensive summary or security brief (e.g., "generate a full report for all certificates")

* Intent Translation: The LangChain layer interprets the scope of the request, converting the text query into structured filtering criteria to locate relevant assets.  

* Data Fetching (ORM): The SQLAlchemy ORM securely builds the dynamic query, interrogates the database, and returns the asset dataset records to the application layer.  

* Report Synthesis (LLM): The raw dataset results are fed back into the LLM context wrapper. The model acts as a technical writer, organizing the asset findings into a highly structured Markdown Report containing executive summaries, finding tables, and localized remediation lists.

**Example prompt:**

```
"generate a full report for all certificates"
```

**Example output:**

```markdown
# Attack Surface Report

## 1. Executive Summary
- Total certificates discovered: **13**.
- **3 stale** and **2 expired** certificates identified, representing immediate operational risk.
- **5 certificates** tagged with `criticality:critical` require heightened monitoring and protection.
- **1 archived** certificate remains in inventory and should be removed to maintain accuracy.

## 2. Asset Inventory
| Asset Type  | Count | Status Breakdown                    |
|-------------|-------|-------------------------------------|
| certificate | 13    | stale: 3, active: 9, archived: 1   |

## 3. Critical Findings
- **3 stale certificates** (CN=cdn.testcorp.com, CN=cdn.internal.dev, CN=CDN.EXAMPLE.COM) indicate outdated TLS endpoints.
- **2 expired certificates** (CN=dev.prod.app.io – expires 2026-06-17; CN=vpn.staging.example.com – expires 2026-05-08) will cause service disruptions.
- **5 critical certificates** (CN=cdn.testcorp.com, CN=dev.prod.app.io, CN=mail.example.com, CN=staging.staging.example.com, CN=CDN.EXAMPLE.COM) require strict key management and regular rotation.
- **1 archived certificate** (CN=TEST.STAGING.EXAMPLE.COM) still present in the active inventory.
- **Multiple certificates** share the same issuer (Sectigo, GlobalSign) and may benefit from consolidated renewal processes.

## 4. Remediation Steps
1. **Replace or remove the 2 expired certificates** (CN=dev.prod.app.io, CN=vpn.staging.example.com) to restore service availability.
2. **Rotate or retire the 3 stale certificates** (CN=cdn.testcorp.com, CN=cdn.internal.dev, CN=CDN.EXAMPLE.COM) to eliminate unused TLS endpoints.
3. **Enforce lifecycle monitoring** for all 5 critical certificates, ensuring quarterly rotation and audit of private keys.
4. **Delete the archived certificate** (CN=TEST.STAGING.EXAMPLE.COM) from the inventory and update associated documentation.
5. **Implement automated certificate discovery and expiration alerts** to prevent future stale/expired certificate exposure.
```

---

## 5. Data Modeling

### 5.1 Asset Model

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Unique, stable identifier |
| `type` | enum | `domain`, `subdomain`, `ip_address`, `service`, `certificate`, `technology` |
| `value` | string | Canonical value e.g. `api.example.com`, `203.0.113.10`, `443/tcp` |
| `status` | enum | `active` · `stale` · `archived` |
| `first_seen` | datetime | Set once on creation, never updated |
| `last_seen` | datetime | Updated on every re-sighting |
| `source` | enum | `import` · `scan` · `manual` |
| `tags` | list\<string\> | Free-form labels for filtering |
| `metadata` | JSON | Type-specific fields: cert issuer/expiry, banner, tech version |

### 5.2 Relationships Graph

| Relationship | Direction | Type |
|---|---|---|
| Subdomain → Domain | one-way | `subdomain_to_domain` |
| Service → IP Address | one-way | `service_to_ip_address` |
| IP Address ↔ Subdomain | bidirectional | `ip_address_to_subdomain` · `subdomain_to_ip_address` |
| Certificate → Domain | one-way | `certificate_to_domain` |
| Certificate → Subdomain | one-way | `certificate_to_subdomain` |
| Technology → Subdomain | one-way | `technology_to_subdomain` |
| Technology → Service | one-way | `technology_to_service` |

### 5.3 Database Schema

DarkAtlas manages integrity and high-performance ingestion through a 4-table transactional layout built on SQLAlchemy:

- **`organizations`** — Holds the primary client/tenant spaces.
- **`assets`** — Houses individual targets (domains, certificates, services) linked explicitly to an organization.
- **`asset_relationships`** — A directed graph table storing mapping keys (e.g., matching a service to its hosting IP).
- **`asset_import_batches`** — Manages operational metrics and asynchronous batch status logs for analytical trackback.

---

## 6. Design Decisions & Assumptions

- **LLM Provider — Cohere (`command-a-plus-05-2026`):** Selected for its production-tier support for structured JSON generation, cost efficiency, and performance with large multi-column retrieval context limits.

- **Filter-based query approach:** To protect against severe security risks associated with dynamically executing raw LLM-generated SQL strings on the live database, the system safely processes queries via LangChain to compile predictable application-level filters.

- **Asynchronous enrichment architecture:** High-volume API endpoints must be non-blocking. The bulk ingestion payload saves raw structures immediately (`201 Created`), spawning background routines to inject metadata progressively without timing out the client.

- **Hardcoded default organization:** To simplify out-of-the-box demo tests on newly initialized empty databases, any batch request referencing the default `00000000-0000-0000-0000-000000000000` UUID is safely processed.

- **Immutable `first_seen`:** `first_seen` is permanently locked upon database insertion to serve as a reliable tracking point, while `last_seen` mutates on subsequent overlapping sight sweeps.

---

## 7. Future Improvements

- **Agentic tool-use:** Elevate the linear LangChain analysis routers into a unified ReAct agent that loops and calls endpoints dynamically.
- **Strict multi-tenant isolation:** Enforce row-level tenant security (RLS) policies within PostgreSQL to strictly ensure multi-tenant protection.
- **Caching layer:** Introduce a Redis instance to cache repeated natural language queries where underlying asset tables haven't mutated.