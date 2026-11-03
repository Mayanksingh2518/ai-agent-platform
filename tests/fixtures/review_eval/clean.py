"""Small helpers with no intended defects."""


def clamp(value: float, low: float, high: float) -> float:
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))


def first_word(text: str) -> str:
    parts = text.split()
    return parts[0] if parts else ""
