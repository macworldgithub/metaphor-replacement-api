# Metaphor Replacement API 🎭

DealerSocket Windows session integration: see [Windows connector setup](docs/dealersocket-windows-session.md).
For read-only terminal snapshots and watch mode, see [live leads worker](docs/dealersocket-live-leads.md).
Use `run_connector.py` for the browser worker (single process, no reload). The text-processing API below is retained.

A modern, high-performance **FastAPI** service for detecting metaphors and idiomatic expressions in text and transforming them into **literal**, **professional**, or **creative** alternatives.

---

## 🚀 Features

- **Metaphor Detection**: Pinpoints idioms and metaphorical phrases within unstructured text with coordinate tracking (`start_pos`, `end_pos`).
- **Multiple Transformation Styles**:
  - `literal`: Replaces idioms with plain, direct explanations (great for accessibility, ESL learners, and technical clarity).
  - `professional`: Replaces colloquial expressions with executive / business-appropriate wording.
  - `creative`: Offers evocative figurative rephrasings.
- **Custom Overrides**: Dynamically map domain-specific metaphors and phrases in single requests.
- **Interactive Documentation**: Auto-generated Swagger UI (`/docs`) and ReDoc (`/redoc`).
- **Comprehensive Test Suite**: Pre-configured with `pytest` and `FastAPI TestClient`.

---

## 📁 Project Structure

```
metaphor-replacement-api/
├── app/
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py          # Pydantic request/response schemas
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health.py           # /health status endpoint
│   │   └── metaphor.py         # /api/v1/metaphors endpoints
│   ├── services/
│   │   ├── __init__.py
│   │   └── metaphor_service.py # Core NLP replacement engine
│   ├── __init__.py
│   ├── config.py               # Pydantic Settings & environment config
│   └── main.py                 # FastAPI application factory
├── tests/
│   ├── __init__.py
│   └── test_main.py            # Automated tests with pytest
├── .env.example                # Example environment variables
├── .gitignore                  # Git ignore rules
├── pyproject.toml              # Build & dependency metadata
├── requirements.txt            # Python dependencies
├── run.py                      # Local development runner
└── README.md                   # Project documentation
```

---

## 🛠️ Quick Start

### 1. Create and Activate a Virtual Environment

In your terminal (`powershell` or `cmd` inside `D:\Hammad\metaphor-replacement-api`):

```powershell
# Create virtual environment
python -m venv .venv

# Activate on Windows PowerShell
.\.venv\Scripts\Activate.ps1

# (Or in CMD)
.\.venv\Scripts\activate.bat
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Run the Development Server

For the DealerSocket browser connector on Windows, use this command instead of the generic development commands below:

```powershell
& "C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe" run_connector.py
```

Do not run the browser connector with `uvicorn --reload`: the Windows reload worker can use an event loop that cannot start Playwright browser subprocesses, resulting in `browser_unavailable` and stale lead snapshots. Run only one connector process on port 8000. The dedicated profile and saved snapshot are preserved across restarts.

Using the runner script:
```powershell
python run.py
```

Or directly via Uvicorn:
```powershell
uvicorn app.main:app --reload --port 8000
```

### 4. Explore Interactive API Docs

Open your browser to:
- **Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 🧪 Running Tests

```powershell
pytest -v
```

---

## 📡 API Endpoints Overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Check service health and version |
| `GET` | `/api/v1/metaphors/catalog` | List all built-in metaphors and their meanings |
| `POST` | `/api/v1/metaphors/analyze` | Detect metaphors, view coordinates, explanations & replacement |
| `POST` | `/api/v1/metaphors/replace` | Directly transform text using specified style or overrides |
