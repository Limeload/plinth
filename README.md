# Plinth

Construction intelligence platform for Indian real estate builders. Plinth replaces Excel cost tracking and manual draw report production with automated financial intelligence — project health scores, one-click bank draw reports, and real-time cash-flow forecasts.

**Stack:** Flask + SQLAlchemy (backend) · React 18 + TypeScript + Vite (frontend) · PostgreSQL · Supabase Auth · Claude API

---

## Folder structure

```
plinth/
├── backend/
│   ├── app/
│   │   ├── __init__.py          # App factory (create_app)
│   │   ├── config.py            # Environment configs
│   │   ├── extensions.py        # db, migrate, supabase instances
│   │   ├── models/              # SQLAlchemy models (one file per entity)
│   │   │   ├── project.py
│   │   │   ├── cost_head.py
│   │   │   ├── transaction.py
│   │   │   ├── milestone.py
│   │   │   ├── contractor.py
│   │   │   ├── draw_report.py
│   │   │   └── disbursement.py
│   │   ├── routes/              # Flask Blueprints (one per domain)
│   │   │   ├── auth.py
│   │   │   ├── projects.py
│   │   │   ├── cost_heads.py
│   │   │   ├── transactions.py
│   │   │   ├── milestones.py
│   │   │   ├── draw_reports.py
│   │   │   ├── cashflow.py
│   │   │   └── portfolio.py
│   │   ├── services/            # Business logic (pure functions)
│   │   │   ├── health_score.py  # Weighted health score calculation
│   │   │   ├── draw_report.py   # Draw report generation logic
│   │   │   ├── cashflow.py      # S-curve forecast vs actual
│   │   │   ├── alerts.py        # Overrun alert triggers
│   │   │   ├── csv_ingestion.py # Cost sheet CSV parser + normaliser
│   │   │   ├── tally_parser.py  # Tally export parser
│   │   │   ├── pdf_generator.py # ReportLab bank-format PDF export
│   │   │   └── ai_insights.py   # Claude API calls
│   │   └── utils/
│   │       ├── auth.py          # JWT helpers
│   │       └── validators.py
│   ├── migrations/              # Alembic migrations (Flask-Migrate)
│   ├── tests/
│   │   ├── test_health_score.py
│   │   ├── test_draw_report.py
│   │   └── test_csv_ingestion.py
│   ├── requirements.txt
│   ├── Procfile                 # Railway deployment
│   └── .env.example
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── Dashboard/
    │   │   │   ├── ProjectHealthCard.tsx
    │   │   │   ├── CostVarianceChart.tsx   # Recharts bar chart
    │   │   │   ├── CashflowChart.tsx       # S-curve line chart
    │   │   │   └── AlertBanner.tsx
    │   │   ├── DrawReport/
    │   │   │   ├── DrawReportTable.tsx
    │   │   │   └── DrawReportPDFButton.tsx
    │   │   ├── Milestones/
    │   │   │   └── MilestoneTracker.tsx
    │   │   ├── Portfolio/
    │   │   │   └── PortfolioView.tsx
    │   │   └── Onboarding/
    │   │       └── CSVUpload.tsx           # React Dropzone CSV upload
    │   ├── pages/
    │   │   ├── Login.tsx
    │   │   ├── Portfolio.tsx
    │   │   ├── ProjectDashboard.tsx
    │   │   ├── DrawReports.tsx
    │   │   ├── Milestones.tsx
    │   │   ├── Contractors.tsx
    │   │   └── Onboarding.tsx
    │   ├── api/
    │   │   └── client.ts        # Axios instance + all typed API calls
    │   ├── context/
    │   │   └── AuthContext.tsx  # Supabase session + React Context
    │   └── types/
    │       └── index.ts         # TypeScript interfaces for all DB models
    ├── tailwind.config.js
    ├── tsconfig.json
    ├── package.json
    └── .env.example
```

---

## Getting started

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for local PostgreSQL + Redis)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # fill in values

flask db upgrade            # run migrations
flask run                   # starts on http://localhost:5000
```

API prefix: `http://localhost:5000/api/v1`

### Frontend

```bash
cd frontend
npm install

cp .env.example .env.local  # fill in values

npm run dev                 # starts on http://localhost:5173
```

### Local database (Docker)

```bash
docker run -d \
  --name plinth-db \
  -e POSTGRES_DB=plinth_db \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -p 5432:5432 \
  postgres:15
```

---

## Environment variables

| File | Copy from |
|------|-----------|
| `backend/.env` | `backend/.env.example` |
| `frontend/.env.local` | `frontend/.env.example` |

Never commit `.env` files — they are in `.gitignore`.

---

## API

All routes are prefixed `/api/v1/` and require `Authorization: Bearer <jwt>` except `/auth/*`.

Key endpoints:

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/projects/:id/health` | Health score + breakdown |
| `GET` | `/projects/:id/summary` | AI-generated plain-language summary |
| `POST` | `/projects/:id/draw-reports/generate` | Auto-generate draw report |
| `GET` | `/projects/:id/draw-reports/:rid/pdf` | Download bank-format PDF |
| `POST` | `/projects/:id/import/csv` | Upload and process cost sheet CSV |
| `GET` | `/portfolio` | All projects with health scores |
| `GET` | `/portfolio/alerts` | Active overrun alerts across portfolio |

---

## Deployment

| Service | Platform |
|---------|----------|
| Frontend | Vercel (auto-deploy from `main`) |
| Backend + DB | Railway |
| Object storage | AWS S3 (CSVs, generated PDFs) |

---

## Contributing

1. Branch off `main` — name it `feature/`, `fix/`, or `chore/`
2. Write tests for any new service logic
3. Open a PR — CI runs tests automatically
