def constant_time_is_equal(a, b):
    """
    To prevent timing attacks, check first that strings are of equal length and then perform bitwise constant time comparison operation, returning zero only if two values are equal.
    """
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0
