"""Run bounded view retention maintenance (repeat periodically from an operator job)."""

from app.core.config import get_settings
from app.db.session import Database
from app.modules.videos.views import ViewService


def main():
    database = Database(get_settings())
    try:
        ViewService(database.session_factory).cleanup()
    finally:
        database.close()


if __name__ == "__main__":
    main()
