# Millington Cakes — Pricing Manager

Internal web application for managing ingredient prices, recipes, and cake cost calculations.

Built with [Streamlit](https://streamlit.io) and [Supabase](https://supabase.com).

---

## Local development setup

**1. Clone the repository**
```bash
git clone https://github.com/your-username/millington-pricing.git
cd millington-pricing
```

**2. Create a virtual environment and install dependencies**
```bash
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**3. Set up credentials**
```bash
cp .env.template .env
```
Edit `.env` and fill in your Supabase project URL and service role key.
These are found in your Supabase dashboard under Settings → API.

**4. Run the database migration** (first time only)
```bash
python migrate.py
```

**5. Run the app**
```bash
streamlit run app/main.py
```
The app opens at `http://localhost:8501`

---

## Deployment

The app is deployed on [Streamlit Community Cloud](https://share.streamlit.io).

Credentials are managed via Streamlit's secrets interface — do not use `.env`
in production. In the Streamlit Cloud dashboard, go to your app →
Settings → Secrets and add:

```toml
SUPABASE_URL = "https://your-project-id.supabase.co"
SUPABASE_KEY = "your-service-role-key"
```

---

## Project structure

```
millington-pricing/
├── app/
│   └── main.py             # Streamlit application (entry point)
├── data/
│   ├── recipes.json        # Source data — reference only
│   └── consumables.json    # Source data — reference only
├── schema.sql              # Supabase database schema
├── migrate.py              # One-time data migration script
├── requirements.txt        # Python dependencies
├── .env.template           # Credentials template (copy to .env)
└── .gitignore
```

---

## Database

Hosted on Supabase (PostgreSQL). Schema is defined in `schema.sql`.
Run this once in the Supabase SQL editor before running the migration script.
