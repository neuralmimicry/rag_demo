import re

def sanitize_test(s):
    # 0. Preliminary cleanup of mis-hallucinated backslashes before quotes
    s = re.sub(r'(?<!\\)\\(["\'])', r'\1', s)

    # 0.7 Fix quoted field names like "project" = "PROJ" -> project = "PROJ"
    s = re.sub(r'["\'](\w+)["\']\s*([=~><!]=?|!~|(?:\b(?:NOT\s+)?(?:IN|IS|WAS|CHANGED)\b))', r'\1 \2', s, flags=re.IGNORECASE)

    # 12. Escape parentheses in contains (~) values if not already escaped
    def escape_parens_in_contains(match):
        prefix = match.group(1)
        field = match.group(2)
        op = match.group(3)
        quote = match.group(4)
        value = match.group(5)
        # Escape ( and ) if they are not preceded by \
        val_escaped = re.sub(r'(?<!\\)\(', r'\\(', value)
        val_escaped = re.sub(r'(?<!\\)\)', r'\\)', val_escaped)
        return f"{prefix}{field} {op} {quote}{val_escaped}{quote}"

    s = re.sub(r'(^|[\s(])(\w+)\s*(!?~)\s*(["\'])(.*?)\4', escape_parens_in_contains, s, flags=re.DOTALL)
    return s

tests = [
    ' "creator" = "Das" ',
    ' "project" IN ("A", "B") ',
    ' text ~ "Payment (Basic)" ',
    ' text ~ "Already \\(Escaped\\)" ',
    ' "summary" ~ "Something (Nested)" AND "status" = "Open" ',
    '(text ~ "A (B)" OR text ~ "C (D)")'
]

for t in tests:
    print(f"INPUT:  {t}")
    print(f"RESULT: {sanitize_test(t)}")
    print("-" * 20)
