# News Aggregator - Clean Installation

This is a cleaned version with only essential files.

## What's Included

- **Core code**: `archiver/` module (config.py, database.py, feeds.py)
- **Main scripts**: run.py, web.py, backfill.py
- **Configuration**: config.json, requirements.txt
- **Data**: Database and downloaded images
- **Utilities**: Testing and discovery tools

## What's Excluded

- Documentation (README files) - refer to original folder or Claude chat
- Backups (config.json.backup.*)
- Python cache (__pycache__)
- Test/fix scripts
- Virtual environment (recreate with setup below)

## Setup

### 1. Create Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Optional: Install Playwright

```powershell
pip install playwright
playwright install chromium
```

## Usage

### Fetch Articles

```powershell
# Single source
python run.py --source shasta-scout

# All sources
python run.py

# Continuous monitoring
python run.py --continuous --interval 30
```

### Web Interface

```powershell
python web.py
# Open http://localhost:5000
```

### Historical Backfill

```powershell
# Get 2 years of articles
python backfill.py --source record-searchlight --years 2
```

## Configuration

Edit `config.json` to:
- Add/remove news sources
- Enable paywall bypass: `"bypass_paywall": true`
- Configure RSS URLs

## Database

- Location: `data/news_archive.db`
- SQLite database with FTS5 search
- Images: `data/images/`

## For Full Documentation

Refer to the original project folder or the Claude chat transcript.
