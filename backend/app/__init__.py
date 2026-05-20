import os
from flask import Flask
from flask_cors import CORS

from .config import config
from .extensions import db, migrate


def create_app(config_name: str | None = None) -> Flask:
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    CORS(app)

    db.init_app(app)
    migrate.init_app(app, db)

    # Import models so Flask-Migrate can detect them
    from .models import (  # noqa: F401
        project, cost_head, transaction, milestone,
        contractor, draw_report, disbursement,
    )

    from .routes import auth, projects, cost_heads, transactions
    from .routes import milestones, draw_reports, cashflow, portfolio, contractors

    app.register_blueprint(auth.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(cost_heads.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(milestones.bp)
    app.register_blueprint(draw_reports.bp)
    app.register_blueprint(cashflow.bp)
    app.register_blueprint(portfolio.bp)
    app.register_blueprint(contractors.bp)

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app
