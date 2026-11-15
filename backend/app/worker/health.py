from app.core.config import get_settings
from app.db.session import Database


def main():
    database = Database(get_settings())
    try:
        database.check_worker_ready()
    except Exception:
        raise SystemExit("Worker heartbeat unavailable") from None
    finally:
        database.close()


if __name__ == "__main__":
    main()
