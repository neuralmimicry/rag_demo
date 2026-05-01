import re
s = '"project" = "PROJ" AND "assignee" = "Das"'
reg = r'["\'](\w+)["\']\s*([=~><!]=?|!~|(?:\b(?:NOT\s+)?(?:IN|IS|WAS|CHANGED)\b))'
res = re.sub(reg, r'\1 \2', s, flags=re.IGNORECASE)
print(f"INPUT: {s}")
print(f"RES:   {res}")
