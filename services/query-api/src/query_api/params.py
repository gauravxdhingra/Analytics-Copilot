"""Translate `$1`-style placeholders (the Query API contract) into psycopg's named form."""

from sqlglot import TokenType, tokenize
from sqlglot.errors import TokenError


def translate_params(sql: str) -> tuple[str, int]:
    """Return (sql for psycopg, number of parameters).

    Uses the SQL tokenizer rather than a regex, so `$1` inside a string literal or comment is
    left alone. Every `%` is doubled because psycopg treats `%` as special anywhere in the text.
    """
    try:
        tokens = tokenize(sql, dialect="postgres")
    except TokenError as exc:
        raise ValueError("cannot tokenize SQL") from exc

    spans: list[tuple[int, int, int]] = []
    for position, token in enumerate(tokens[:-1]):
        following = tokens[position + 1]
        if (
            token.token_type == TokenType.PARAMETER
            and token.text == "$"
            and following.token_type == TokenType.NUMBER
            and following.start == token.end + 1
            and following.text.isdigit()
        ):
            spans.append((token.start, following.end + 1, int(following.text)))

    indices = {index for _, _, index in spans}
    highest = max(indices, default=0)
    if indices != set(range(1, highest + 1)):
        raise ValueError("placeholder numbering has a gap")

    out: list[str] = []
    cursor = 0
    for start, end, index in spans:
        out.append(sql[cursor:start].replace("%", "%%"))
        out.append(f"%(p{index})s")
        cursor = end
    out.append(sql[cursor:].replace("%", "%%"))
    return "".join(out), highest
