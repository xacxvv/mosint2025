"""
Demo MOSINT (social-media OSINT) web interface.

This Flask application provides a simple analytic UI for querying Facebook-style
profile, post, comment, reaction, and phone datasets. It is designed for
research and training purposes only.
"""
import logging
import os
import secrets
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


class Config:
    """Application configuration with environment overrides."""

    # Database settings
    DB_NAME = os.getenv("MOSINT_DB_NAME", "mosint")
    DB_USER = os.getenv("MOSINT_DB_USER", "mosint")
    DB_PASSWORD = os.getenv("MOSINT_DB_PASSWORD", "changeme")
    DB_HOST = os.getenv("MOSINT_DB_HOST", "localhost")
    DB_PORT = os.getenv("MOSINT_DB_PORT", "5432")

    # Security
    SECRET_KEY = os.getenv("MOSINT_SECRET_KEY", secrets.token_hex(16))

    # Demo admin credentials (can be moved to environment variables later)
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "admin"

    # Dataset size estimates (static values for UI display)
    DATASET_COUNTS = {
        "profiles": 2_917_000,
        "posts": 80_611_000,
        "comments": 82_910_000,
        "reactions": 138_111_000,
        "eightdigit": 19_705_000,
    }


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    configure_logging(app)
    return app


def configure_logging(app: Flask) -> None:
    """Configure basic application logging."""

    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app.logger.setLevel(log_level)


app = create_app()


def get_db_connection():
    """Create and return a new psycopg2 database connection."""

    return psycopg2.connect(
        dbname=app.config["DB_NAME"],
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        host=app.config["DB_HOST"],
        port=app.config["DB_PORT"],
    )


def login_required(view_func):
    """Decorator to ensure the user is authenticated."""

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_authenticated"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if (
            username == app.config["ADMIN_USERNAME"]
            and password == app.config["ADMIN_PASSWORD"]
        ):
            session["is_authenticated"] = True
            flash("Signed in successfully.", "success")
            return redirect(url_for("index"))

        flash("Invalid credentials. Please try again.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    results: Dict[str, List[Tuple[Any, ...]]] = {}
    column_names: Dict[str, List[str]] = {}
    uid = ""
    phone = ""
    profile_summary: Optional[Dict[str, int]] = None
    reaction_stats: List[Dict[str, Any]] = []

    if request.method == "POST":
        uid = request.form.get("uid", "").strip()
        phone = request.form.get("phone", "").strip()

        validation_error = validate_inputs(uid, phone)
        if validation_error:
            flash(validation_error, "warning")
            return render_template(
                "index.html",
                counts=app.config["DATASET_COUNTS"],
                results=results,
                colnames=column_names,
                uid=uid,
                phone=phone,
                profile_summary=profile_summary,
                reaction_stats=reaction_stats,
            )

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if phone:
                        query_phone(cur, phone, results, column_names)
                    elif uid:
                        query_by_uid(cur, uid, results, column_names)
                        profile_summary = summarize_profile_activity(results)
                        reaction_stats = fetch_reaction_stats(cur, uid)
        except psycopg2.Error as exc:
            app.logger.error("Database error while processing request: %s", exc)
            flash("An error occurred while fetching data. Please try again later.", "danger")

    return render_template(
        "index.html",
        counts=app.config["DATASET_COUNTS"],
        results=results,
        colnames=column_names,
        uid=uid,
        phone=phone,
        profile_summary=profile_summary,
        reaction_stats=reaction_stats,
    )


def validate_inputs(uid: str, phone: str) -> Optional[str]:
    """Validate search inputs and return an error message when invalid."""

    if uid and phone:
        return "Please search by UID or phone, not both at the same time."

    if not uid and not phone:
        return "Enter a UID or an 8-digit phone number to search."

    if phone:
        if not phone.isdigit() or len(phone) != 8:
            return "Phone number must be exactly 8 digits."

    return None


def query_phone(
    cursor, phone: str, results: Dict[str, List[Tuple[Any, ...]]], column_names: Dict[str, List[str]]
) -> None:
    """Query phone mapping table by eight-digit phone number."""

    cursor.execute("SELECT * FROM fbphone WHERE eightdigitnumbers = %s", (phone,))
    column_names["fbphone"] = [desc[0] for desc in cursor.description]
    results["fbphone"] = cursor.fetchall()


def query_by_uid(
    cursor, uid: str, results: Dict[str, List[Tuple[Any, ...]]], column_names: Dict[str, List[str]]
) -> None:
    """Query all related tables by UID."""

    lookup_queries = {
        "profiles": ("SELECT * FROM profiles WHERE profile_id = %s", uid),
        "posts": ("SELECT * FROM posts WHERE post_user_id = %s", uid),
        "comments": ("SELECT * FROM comments WHERE com_user_id = %s", uid),
        "reactions": ("SELECT * FROM reactions WHERE reac_user_id = %s", uid),
        "fbphone": ("SELECT * FROM fbphone WHERE uid = %s", uid),
    }

    for key, (sql, param) in lookup_queries.items():
        cursor.execute(sql, (param,))
        column_names[key] = [desc[0] for desc in cursor.description]
        results[key] = cursor.fetchall()


def summarize_profile_activity(results: Dict[str, List[Tuple[Any, ...]]]) -> Dict[str, int]:
    """Compute post, comment, and reaction counts for a profile."""

    return {
        "post_count": len(results.get("posts", [])),
        "comment_count": len(results.get("comments", [])),
        "reaction_count": len(results.get("reactions", [])),
    }


def fetch_reaction_stats(cursor, uid: str) -> List[Dict[str, Any]]:
    """Return aggregated reaction counts per reaction type for a UID."""

    cursor.execute(
        """
        SELECT reac_type, COUNT(*) AS cnt
        FROM reactions
        WHERE reac_user_id = %s
        GROUP BY reac_type
        ORDER BY cnt DESC
        """,
        (uid,),
    )

    return [{"type": reac_type, "count": cnt} for reac_type, cnt in cursor.fetchall()]


if __name__ == "__main__":
    # Flask's built-in server; use a production-ready server like gunicorn in real deployments.
    app.run(host="0.0.0.0", port=5000, debug=True)
