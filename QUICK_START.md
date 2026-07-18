# QUICK START - HousePadi Backend

This file provides the minimal steps to run the backend locally for development and testing.

1. Clone repository
```bash
git clone git@github.com:kushalfayeed123/house-padi-engine-v2.git
cd house-padi-engine-v2
git checkout agents/multi-agent-property-rental-system
```

2. Create virtualenv and install
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

3. Configure environment (.env)
- Copy .env.example to .env and fill values:
  - SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  - GROQ_API_KEY
  - BACKEND_JWT_SECRET

4. Start Ollama (local model)
```bash
# Ensure Ollama is installed and running
ollama run llama3.1:8b
```

5. Start backend
```bash
# Preferred: use the 'uv' wrapper (install with `pip install uv`)
uv run --env-file .env uvicorn app.main:app --host 0.0.0.0 --port 8000  --proxy-headers --reload           
# Fallback: use uvicorn directly if 'uv' is not available
uvicorn app.main:app --reload
```

6. Open API docs
- http://localhost:8000/docs

7. Run test scenarios
- See TESTING_GUIDE.md for step-by-step flows

Notes:
- For production, ensure Supabase/Postgres with PostGIS is configured and Ollama is available on the host.
- Payment gateway integration is required for real transactions (Stripe/Flutterwave).
