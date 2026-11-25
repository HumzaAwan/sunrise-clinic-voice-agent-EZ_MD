"""Deterministic parsers for spoken identity. The model does not verify anyone."""

import re
from datetime import date

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30, "thirty-first": 31, "thirty first": 31,
}
for _tens_name, _tens_value in (("twenty", 20), ("thirty", 30)):
    for _unit_name, _unit_value in (
        ("first", 1), ("second", 2), ("third", 3), ("fourth", 4), ("fifth", 5),
        ("sixth", 6), ("seventh", 7), ("eighth", 8), ("ninth", 9),
    ):
        _ORDINALS[f"{_tens_name} {_unit_name}"] = _tens_value + _unit_value
        _ORDINALS[f"{_tens_name}-{_unit_name}"] = _tens_value + _unit_value

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_UNDER_100 = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_FILLER = {
    "please", "my", "name", "is", "this", "date", "of", "birth", "born", "last",
    "four", "digits", "digit", "social", "security", "number", "ssn", "the", "and",
    "um", "uh", "umm", "ah", "oh", "a", "on", "its", "it's", "im", "i'm", "caller",
    "patient",     "okay", "ok", "are", "was", "were", "yeah", "yes", "so", "hi", "hello",
    "hey", "well", "just", "right", "alright", "here", "no", "not", "nope",
    "rate", "full", "in", "this", "period", "phone", "decades", "decade",
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _strip_span(text: str, start: int, end: int) -> str:
    return f"{text[:start]} {text[end:]}"


def _parse_under_100(tokens: list[str], index: int) -> tuple[int | None, int]:
    if index >= len(tokens) or tokens[index] not in _UNDER_100:
        return None, index
    value = _UNDER_100[tokens[index]]
    index += 1
    if value >= 20 and index < len(tokens) and tokens[index] in _UNDER_100 and _UNDER_100[tokens[index]] < 10:
        value += _UNDER_100[tokens[index]]
        index += 1
    return value, index


def _year_from_tokens(tokens: list[str], index: int) -> tuple[int | None, int]:
    if index >= len(tokens):
        return None, index
    if re.fullmatch(r"(19|20)\d{2}", tokens[index]):
        return int(tokens[index]), index + 1
    if tokens[index] in {"nineteen", "twenty"}:
        century = 1900 if tokens[index] == "nineteen" else 2000
        rest, nxt = _parse_under_100(tokens, index + 1)
        if rest is not None and rest < 100:
            return century + rest, nxt
    return None, index


def _day_from_token(token: str) -> int | None:
    if re.fullmatch(r"\d{1,2}", token):
        day = int(token)
        return day if 1 <= day <= 31 else None
    return _ORDINALS.get(token)


def extract_dob(text: str) -> tuple[date | None, str]:
    """Return a date and the text with that date removed."""
    raw = text.lower().replace(",", " ")
    iso = re.search(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b", raw)
    if iso:
        found = _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if found:
            return found, _strip_span(raw, iso.start(), iso.end())
    slash = re.search(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})\b", raw)
    if slash:
        found = _safe_date(int(slash.group(3)), int(slash.group(1)), int(slash.group(2)))
        if found:
            return found, _strip_span(raw, slash.start(), slash.end())
    named = re.search(
        rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+((?:19|20)\d{{2}})\b",
        raw,
    )
    if named:
        found = _safe_date(int(named.group(3)), _MONTHS[named.group(1)], int(named.group(2)))
        if found:
            return found, _strip_span(raw, named.start(), named.end())
    of_form = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+({_MONTH_ALT})\s+((?:19|20)\d{{2}})\b",
        raw,
    )
    if of_form:
        found = _safe_date(int(of_form.group(3)), _MONTHS[of_form.group(2)], int(of_form.group(1)))
        if found:
            return found, _strip_span(raw, of_form.start(), of_form.end())
    day_first = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\s+((?:19|20)\d{{2}})\b",
        raw,
    )
    if day_first:
        found = _safe_date(int(day_first.group(3)), _MONTHS[day_first.group(2)], int(day_first.group(1)))
        if found:
            return found, _strip_span(raw, day_first.start(), day_first.end())

    normalized = raw.replace("-", " ")
    tokens = re.findall(r"[a-z0-9']+", normalized)
    for index, token in enumerate(tokens):
        if token not in _MONTHS:
            continue
        if index + 1 >= len(tokens):
            continue
        day = _day_from_token(tokens[index + 1])
        if day is None and index + 2 < len(tokens):
            pair = f"{tokens[index + 1]} {tokens[index + 2]}"
            day = _ORDINALS.get(pair)
            year_at = index + 3 if day else None
        else:
            year_at = index + 2 if day else None
        if day is None or year_at is None:
            continue
        year, _used = _year_from_tokens(tokens, year_at)
        if year is None:
            continue
        found = _safe_date(year, _MONTHS[token], day)
        if found:
            spoken = " ".join(tokens[index:year_at]) + " " + " ".join(tokens[year_at:_used])
            cleaned = normalized.replace(spoken.strip(), " ", 1)
            return found, cleaned
    return None, raw


def _edits(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > 2:
        return 3
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, start=1):
        current = [index]
        for other_index, other in enumerate(right, start=1):
            cost = 0 if char == other else 1
            current.append(min(previous[other_index] + 1, current[other_index - 1] + 1, previous[other_index - 1] + cost))
        previous = current
    return previous[-1]


def extract_last4(text: str) -> tuple[str | None, str]:
    collapsed = re.sub(r"(?<=\d),\s*(?=\d)", "", text)
    collapsed = re.sub(r"\b(\d)\s+(\d{3})\b", r"\1\2", collapsed)
    groups = [(match.group(1), match.start(), match.end()) for match in re.finditer(r"\b(\d+)\b", collapsed)]
    fours = [
        item for item in groups if len(item[0]) == 4 and not item[0].startswith(("19", "20"))
    ]
    if fours:
        digits, start, end = fours[-1]
        return digits, _strip_span(collapsed, start, end)
    singles = [item for item in groups if len(item[0]) == 1]
    run: list[tuple[str, int, int]] = []
    best: list[tuple[str, int, int]] = []
    previous_end = -1
    for item in singles:
        if previous_end >= 0 and item[1] - previous_end > 3:
            if len(run) >= 4:
                best = run
            run = []
        run.append(item)
        previous_end = item[2]
    if len(run) >= 4:
        best = run
    if len(best) >= 4:
        chosen = best[-4:]
        cleaned = collapsed
        for _digits, start, end in reversed(chosen):
            cleaned = _strip_span(cleaned, start, end)
        return "".join(item[0] for item in chosen), cleaned
    tokens = collapsed.lower().split()
    sequence: list[str] = []
    for token in tokens:
        if token in _DIGIT_WORDS:
            sequence.append(_DIGIT_WORDS[token])
            if len(sequence) == 4:
                cleaned = text
                for word in tokens:
                    if word in _DIGIT_WORDS:
                        cleaned = re.sub(rf"\b{word}\b", " ", cleaned, count=1)
                return "".join(sequence), cleaned
        elif sequence:
            sequence = []
    return None, text


def extract_name(text: str) -> str | None:
    tokens = re.findall(r"[a-z']+", text.lower())
    kept = [token for token in tokens if token not in _FILLER and token not in _MONTHS and token not in _DIGIT_WORDS]
    if len(kept) < 2:
        return None
    return " ".join(kept[:4])


def spoken_year(text: str) -> int | None:
    """A year spoken by itself, used to correct a date already heard."""
    raw = text.lower().replace(",", " ").replace("-", " ")
    numeric = re.findall(r"\b((?:19|20)\d{2})\b", raw)
    if len(numeric) == 1:
        return int(numeric[0])
    tokens = re.findall(r"[a-z0-9']+", raw)
    for index, _token in enumerate(tokens):
        year, _used = _year_from_tokens(tokens, index)
        if year is not None and 1900 <= year <= 2010:
            return year
    return None


def read_identity_parts(text: str) -> tuple[str | None, date | None, str | None]:
    """Pull whichever of name, date of birth, and last four are in this sentence."""
    _dob, rest = extract_dob(text)
    last4, rest = extract_last4(rest)
    return extract_name(rest), _dob, last4


def parse_identity(text: str) -> tuple[str, date, str] | None:
    dob, rest = extract_dob(text)
    if dob is None:
        return None
    last4, rest = extract_last4(rest)
    if last4 is None:
        return None
    name = extract_name(rest)
    if name is None:
        return None
    return name, dob, last4


def parse_date_only(text: str) -> date | None:
    found, _rest = extract_dob(text)
    return found


def normalize_name(value: str) -> str:
    cleaned = value.casefold().replace(",", " ")
    cleaned = re.sub(r"[^a-z\s]", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _name_close(spoken: str, recorded: str) -> bool:
    left = normalize_name(spoken)
    right = normalize_name(recorded)
    if left == right:
        return True
    if not left or not right or left[0] != right[0]:
        return False
    return _edits(left, right) <= 1


def names_match(spoken: str, first: str, last: str) -> bool:
    parts = normalize_name(spoken).split()
    if len(parts) < 2:
        return False
    start, end = parts[0], parts[-1]
    forward = _name_close(start, first) and _name_close(end, last)
    reverse = _name_close(start, last) and _name_close(end, first)
    return forward or reverse


def _phone_digits(text: str) -> str:
    """Pull a 10-digit phone from arabic or spoken digits ('four two seven…')."""
    lowered = text.lower()
    # STT often inserts "to"/"too" between digit groups: "4 to 7" → keep both digits.
    lowered = re.sub(r"(?<=\d)\s*(?:to|too)\s*(?=\d)", " ", lowered)
    pieces: list[str] = []
    for token in re.findall(r"[a-z0-9]+", lowered):
        if token.isdigit():
            pieces.append(token)
        elif token in _DIGIT_WORDS:
            pieces.append(_DIGIT_WORDS[token])
    return "".join(pieces)


def parse_phone(text: str) -> str | None:
    digits = _phone_digits(text)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    # Prefer a clean 3-3-4 group when extra digits appear around the number.
    match = re.search(r"\b(\d{3})[-.\s]?(\d{3})[-.\s]?(\d{4})\b", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if len(digits) > 10:
        # Take the last 10 digits (common when a country code sneaks in).
        tail = digits[-10:]
        return f"{tail[:3]}-{tail[3:6]}-{tail[6:]}"
    return None


def phone_digit_count(text: str) -> int:
    return len(_phone_digits(text))


def spoken_email(text: str) -> str | None:
    """Turn a spoken address into an email. 'alex dot rivera at example dot com'."""
    lowered = text.lower().replace(",", " ")
    for marker in (
        "email address is",
        "email is",
        "address is",
        "email address",
        "my email",
        "have my email",
    ):
        if marker in lowered:
            lowered = lowered.split(marker, 1)[1]
            break
    lowered = (
        lowered.replace(" at the rate ", " at ")
        .replace(" at the @ ", " at ")
        .replace(" at rate ", " at ")
        .replace(" dot ", ".")
        .replace(" period ", ".")
    )
    if " at " not in lowered and "@" not in lowered:
        return None
    if "@" in lowered:
        left, right = lowered.split("@", 1)
    else:
        left, right = lowered.rsplit(" at ", 1)
    skip = {
        "the", "a", "my", "is", "and", "please", "uh", "um", "yeah", "yes",
        "i", "have", "email", "address", "for", "its", "it's",
    }
    local_words = [word for word in re.findall(r"[a-z0-9.]+", left) if word not in skip]
    domain_words = [word for word in re.findall(r"[a-z0-9.]+", right) if word not in skip]
    if not local_words or not domain_words:
        return None
    local = ".".join(piece for word in local_words for piece in word.strip(".").split(".") if piece)
    domain = "".join(word.strip(".") for word in domain_words)
    if "." not in domain:
        return None
    email = f"{local}@{domain}"
    if re.fullmatch(r"[a-z0-9.]+@[a-z0-9.]+\.[a-z]{2,}", email):
        return email
    return None


def parse_email(text: str) -> str | None:
    match = re.search(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", text)
    if not match:
        return None
    return match.group(1).lower()


def parse_person_name(text: str) -> str | None:
    tokens = re.findall(r"[A-Za-z']+", text)
    skip = {word.capitalize() if False else word.lower() for word in _FILLER}
    kept = [token for token in tokens if token.lower() not in skip and token.lower() not in _MONTHS]
    if len(kept) < 2:
        return None
    return " ".join(kept[:4])
