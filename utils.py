import toml


def constant_time_is_equal(a, b):
    """To prevent timing attacks, check first that strings are of equal length
    and then perform bitwise constant time comparison operation, returning zero
    only if two values are equal."""
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


def get_package_version() -> str:
    # importing here, to avoid circular import error
    from main import app
    from main import cache

    with app.app_context():
        package_version = cache.get("package_version")

        if not package_version:
            with open("pyproject.toml", "r") as toml_file:
                data = toml.load(toml_file)

            package_version = data.get("tool", {}).get("poetry", {}).get("version")
            cache.set("package_version", package_version)

        return package_version
