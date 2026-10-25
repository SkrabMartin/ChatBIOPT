"""Account administration from the shell (the first admin cannot be created through the API).

    python -m app.cli create-user --email you@example.org --name "Your Name" --admin [--claim-unowned-projects]

The password is read from BIORESEARCH_PASSWORD or prompted for. `--claim-unowned-projects` makes the user owner of
every project that has no members (projects created before accounts existed are otherwise inaccessible).
"""

import argparse
import asyncio
import getpass
import os
import sys

from pydantic import ValidationError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.service import AuthService
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import create_engine
from app.models import Project, ProjectMember
from app.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, UserCreate


async def create_user(email: str, name: str, password: str, *, admin: bool, claim: bool) -> None:
    data = UserCreate(email=email, display_name=name, password=password, is_admin=admin)
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = await AuthService(session, settings).create_user(
                data.email, data.display_name, data.password, is_admin=data.is_admin
            )
            print(f"created {user.email} ({'admin' if user.is_admin else 'user'}) id={user.id}")
            if claim:
                unowned = list(
                    await session.scalars(
                        select(Project.id).where(~exists().where(ProjectMember.project_id == Project.id))
                    )
                )
                session.add_all(ProjectMember(project_id=pid, user_id=user.id, role="owner") for pid in unowned)
                await session.commit()
                print(f"claimed {len(unowned)} project(s) without members")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="create an account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="display name")
    create.add_argument("--admin", action="store_true", help="may manage accounts (grants no project access)")
    create.add_argument("--claim-unowned-projects", action="store_true")
    args = parser.parse_args()

    password = os.environ.get("BIORESEARCH_PASSWORD") or getpass.getpass("Password: ")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        sys.exit(f"password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters")
    try:
        asyncio.run(create_user(args.email, args.name, password, admin=args.admin, claim=args.claim_unowned_projects))
    except (AppError, ValidationError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
