import random
import secrets
import string

from strands import tool


@tool
def generate_name() -> str:
    """Generate a random first and last name."""
    first_names = [
        "Alex", "Jordan", "Taylor", "Morgan", "Casey",
        "Riley", "Avery", "Cameron", "Drew", "Jamie",
    ]

    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Davis",
        "Miller", "Wilson", "Moore", "Anderson", "Thomas",
    ]

    return f"{random.choice(first_names)} {random.choice(last_names)}"


@tool
def generate_username(length: int = 12) -> str:
    """Generate a random username string."""
    characters = string.ascii_lowercase + string.digits

    return "".join(secrets.choice(characters) for _ in range(length))

@tool
def generate_password(length: int = 16) -> str:
    """Generate a cryptographically secure random password."""
    if length < 8:
        raise ValueError("Password length must be at least 8 characters.")

    characters = string.ascii_letters + string.digits + "!@#$%^&*"

    # secrets is appropriate for password generation.
    password = "".join(secrets.choice(characters) for _ in range(length))

    return password