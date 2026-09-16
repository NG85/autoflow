import secrets
import asyncio
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from colorama import Fore, Style
import click

from app.core.db import get_db_async_session_context
from app.models import User, ChatEngine


async def ensure_admin_user(
    session: AsyncSession, email: str | None = None, password: str | None = None
) -> None:
    result = await session.exec(select(User).where(User.is_superuser == True))
    user = result.first()
    if not user:
        from app.auth.registration import ensure_admin_user_account

        admin_email = email or "admin@example.com"
        admin_password = password or secrets.token_urlsafe(16)
        user = await ensure_admin_user_account(
            session,
            email=admin_email,
            password=admin_password,
        )
        print(Fore.RED + "\n" + "!" * 80)
        print(
            Fore.RED + "[IMPORTANT] Admin user created with email: "
            f"{admin_email} and password: {admin_password}"
        )
        print(Fore.RED + "!" * 80 + "\n" + Style.RESET_ALL)
    else:
        print(Fore.YELLOW + "Admin user already exists, skipping...")


async def ensure_system_user(
    session: AsyncSession, *, reset_api_key: bool = False
) -> None:
    user = await _resolve_system_user(session)
    if user is None:
        return
    await ensure_system_api_key(session, user, reset=reset_api_key)


async def _resolve_system_user(session: AsyncSession) -> User | None:
    from app.core.config import settings
    from app.auth.registration import ensure_system_user_account

    email = settings.SYSTEM_USER_EMAIL
    result = await session.exec(
        select(User).where(func.lower(User.email) == func.lower(email))
    )
    existing = result.first()
    if existing:
        if existing.is_superuser:
            print(
                Fore.YELLOW
                + f"System user email {email} already belongs to a superuser, skipping..."
            )
            return None
        print(Fore.YELLOW + "System user already exists, skipping...")
        _ensure_existing_system_oauth(email)
        return existing

    user = await ensure_system_user_account(session, email=email)
    print(
        Fore.GREEN
        + f"System user created with email: {user.email} (non-superuser, for API keys)"
        + Style.RESET_ALL
    )
    return user


def _ensure_existing_system_oauth(email: str) -> None:
    from app.auth.registration import register_system_user_via_oauth

    oauth_result = register_system_user_via_oauth(email=email)
    if oauth_result is None:
        print(Fore.YELLOW + f"System user oauth register failed for {email}")
        return
    if oauth_result.already_existed:
        print(Fore.YELLOW + f"System user oauth account already exists for {email}")
        return
    print(
        Fore.GREEN
        + f"System user registered via oauth: {email}"
        + Style.RESET_ALL
    )


async def ensure_system_api_key(
    session: AsyncSession, user: User, *, reset: bool = False
) -> None:
    from app.auth.api_keys import (
        BOOTSTRAP_SYSTEM_API_KEY_DESCRIPTION,
        api_key_manager,
    )

    api_key, raw_secret = await api_key_manager.ensure_api_key_for_user(
        session,
        user,
        description=BOOTSTRAP_SYSTEM_API_KEY_DESCRIPTION,
        reset=reset,
    )
    if raw_secret:
        action = "rotated" if reset else "created"
        print(Fore.RED + "\n" + "!" * 80)
        print(
            Fore.RED
            + f"[IMPORTANT] System API key {action} for {user.email}\n"
            f"api_key: {raw_secret}\n"
            "Store this secret now; it will not be shown again."
        )
        print(Fore.RED + "!" * 80 + "\n" + Style.RESET_ALL)
        return

    print(
        Fore.YELLOW
        + f"System API key already exists ({api_key.api_key_display}), skipping..."
    )


async def reset_admin_password(
    session: AsyncSession, new_password: str | None = None
) -> None:
    result = await session.exec(select(User).where(User.is_superuser == True))
    user = result.first()
    if not user:
        print(Fore.YELLOW + "Admin user does not exist, skipping reset password...")
    else:
        from app.auth.users import update_user_password

        admin_password = new_password or secrets.token_urlsafe(16)
        updated_user = await update_user_password(
            session,
            user_id=user.id,
            new_password=admin_password,
        )
        print(
            Fore.GREEN + "Admin user password reset SUCCESS!\n"
            f"email: {updated_user.email} \n"
            f"password: {admin_password}" + Style.RESET_ALL
        )


async def ensure_default_chat_engine(session: AsyncSession) -> None:
    result = await session.scalar(func.count(ChatEngine.id))
    if result == 0:
        from app.rag.chat.config import ChatEngineConfig

        chat_engine = ChatEngine(
            name="default",
            engine_options=ChatEngineConfig().model_dump(),
            is_default=True,
        )
        session.add(chat_engine)
        await session.commit()
        print("Default chat engine created.")
    else:
        print(Fore.YELLOW + "Default chat engine already exists, skipping...")


async def bootstrap(
    email: str | None = None,
    password: str | None = None,
    reset_password: bool = False,
    reset_system_api_key: bool = False,
) -> None:
    async with get_db_async_session_context() as session:
        await ensure_admin_user(session, email, password)
        await ensure_system_user(session, reset_api_key=reset_system_api_key)
        await ensure_default_chat_engine(session)
        if reset_password:
            await reset_admin_password(session, password)


@click.command()
@click.option(
    "--email", default=None, help="Admin user email, default=admin@example.com"
)
@click.option(
    "--password", default=None, help="Admin user password, default=random generated"
)
@click.option("--reset-password", "-r", is_flag=True, help="Reset admin user password.")
@click.option(
    "--reset-system-api-key",
    is_flag=True,
    help="Rotate the system user API key (invalidates existing keys for that user).",
)
def main(
    email: str | None,
    password: str | None,
    reset_password: bool,
    reset_system_api_key: bool,
):
    """Bootstrap the application with optional admin credentials."""
    print(Fore.GREEN + "Bootstrapping the application..." + Style.RESET_ALL)
    asyncio.run(
        bootstrap(
            email,
            password,
            reset_password,
            reset_system_api_key,
        )
    )
    print(Fore.GREEN + "Bootstrapping completed." + Style.RESET_ALL)


if __name__ == "__main__":
    main()
