"""Create the initial administrator; passwords are prompted, never command arguments."""

import argparse
from getpass import getpass

from app.core.config import get_settings
from app.db.session import Database
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import Register
from app.modules.auth.service import AuthService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    database = Database(get_settings())
    try:
        with database.session_factory() as db:
            from app.modules.auth.schemas import normalize

            user = AuthRepository(db).by_username(normalize(args.username))
            if user:
                if user.role == "admin" and user.email == normalize(args.email):
                    print("Administrator already exists; unchanged")
                    return
                raise SystemExit("Account conflict; existing accounts are never modified")
    finally:
        database.close()
    password = getpass("Password: ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    try:
        data = Register(email=args.email, username=args.username, password=password)
    except ValueError:
        raise SystemExit("Invalid email, username or password (12–128 characters)") from None
    database = Database(get_settings())
    try:
        with database.session_factory() as db:
            AuthService(db).register(data, role="admin")
            db.commit()
        print("Administrator created; existing accounts are never modified")
    finally:
        database.close()


if __name__ == "__main__":
    main()
