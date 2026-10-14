import argparse
import os

import auth
import database


def parse_args():
    parser = argparse.ArgumentParser(description="Create the initial admin account.")
    parser.add_argument("--username", default=os.getenv("STREAMVAULT_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("STREAMVAULT_ADMIN_PASSWORD"))
    parser.add_argument("--reset", action="store_true", help="Drop and recreate the schema before seeding the admin user.")
    return parser.parse_args()


def seed_admin(username: str, password: str, reset: bool = False):
    if not auth.is_valid_username(username):
        raise ValueError("Admin username must be 3-32 safe filename characters")
    if not password or not auth.is_password_strong(password):
        raise ValueError("Admin password must be at least 8 characters and include upper/lowercase, a number, and a symbol")
    if reset:
        print("Recreating schema from scratch...")
        database.Base.metadata.drop_all(bind=database.engine)
        database.Base.metadata.create_all(bind=database.engine)

    os.makedirs(f"/saas-data/users/{username}", exist_ok=True)

    db = database.SessionLocal()
    try:
        existing = auth.get_user_by_username(db, username)
        if existing:
            print(f"Updating existing admin user: {username}")
            existing.hashed_password = auth.get_password_hash(password)
            existing.is_admin = True
            existing.is_active = True
            user = existing
        else:
            print(f"Creating admin user: {username}")
            user = database.User(
                username=username,
                hashed_password=auth.get_password_hash(password),
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.flush()

        preferences = db.query(database.UserPreferences).filter(database.UserPreferences.user_id == user.id).first()
        if not preferences:
            db.add(
                database.UserPreferences(
                    user_id=user.id,
                    storage_limit_gb=50,
                    subtitle_provider="manual",
                    preferred_quality="1080p",
                )
            )

        db.commit()
        print("--- SUCCESS ---")
        print(f"Username: {username}")
    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    arguments = parse_args()
    if not arguments.password:
        raise SystemExit("Set STREAMVAULT_ADMIN_PASSWORD or pass --password")
    seed_admin(arguments.username, arguments.password, reset=arguments.reset)
