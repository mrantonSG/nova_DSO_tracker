#!/usr/bin/env python3
# Fix the banner pattern in nova/log_parser.py

with open('../nova/log_parser.py', 'r') as f:
    lines = f.readlines()

# Find and replace the banner pattern line (line 841)
for i, line in enumerate(lines):
    if i == 840 and 'banner' in line:
        # Replace the pattern
        lines[i] = \"'banner': re.compile(r'\\*{5,}\\\\s*([A-Z][A-Z\\\\s]*)\\\\*{5,}')\"
        print(f'Line {i+1} replaced')
        break

with open('../nova/log_parser.py', 'w') as f:
    f.writelines(lines)
    print('Pattern line replaced successfully')
