#!/usr/bin/env python3
# Fix the banner pattern in nova/log_parser.py

with open('../nova/log_parser.py', 'r') as f:
    content = f.read()

# Replace the banner pattern
old_pattern = r"'banner': re.compile(r'\\*{8,}\\s*([A-Z][A-Z\\s]*)\\*{5,}')"
new_pattern = r"'banner': re.compile(r'\\*{8,}\\s*([A-Z][A-Z\\s]*)\\*{5,}')"

if old_pattern in content:
    new_content = content.replace(old_pattern, new_pattern)
    with open('../nova/log_parser.py', 'w') as f:
        f.write(new_content)
    print('Pattern line replaced successfully')
else:
    print('Pattern not found')
