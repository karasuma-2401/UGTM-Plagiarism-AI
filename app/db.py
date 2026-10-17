import psycopg
from psycopg.rows import dict_row

from app.config import settings


def connect():
    return psycopg.connect(settings.postgres_dsn, row_factory=dict_row)


def set_status(job_id: str, status: str, error_code: str | None = None):
    with connect() as con:
        con.execute(
            """
            UPDATE integrity.jobs
            SET status = %s, error_code = %s, updated_at = now()
            WHERE id = %s::uuid
            """,
            (status, error_code, job_id),
        )


def get_job(job_id: str):
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM integrity.jobs WHERE id = %s::uuid
            """,
            (job_id,),
        ).fetchone()

