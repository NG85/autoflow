"""Auth user domain exceptions."""


class UserAlreadyExists(Exception):
    """A user with the same email already exists."""


class UserNotExists(Exception):
    """The requested user does not exist."""
