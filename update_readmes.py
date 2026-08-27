import glob
import re

with open("tables.md", "r") as f:
    new_tables = f.read().strip()

files = glob.glob("README*.md")

for fname in files:
    with open(fname, "r") as f:
        content = f.read()

    # Find the start of the Recommended Presets section.
    # It starts with "### 📊 " and is immediately before the table containing "XAUUSD"
    
    # Let's find the section that has the table
    # Regex: find '### 📊 [^\n]+' that is followed by something and then a table with XAUUSD
    # ending before the next '### '
    
    pattern = re.compile(r'(### 📊 [^\n]*\n.*?)(?=\n### )', re.DOTALL)
    
    def replacer(match):
        text = match.group(1)
        if 'XAUUSD' in text and '|' in text:
            # This is the presets section!
            # We want to replace it with the new_tables
            return new_tables
        return text

    new_content = pattern.sub(replacer, content)
    
    if new_content != content:
        with open(fname, "w") as f:
            f.write(new_content)
        print(f"Updated {fname}")
    else:
        print(f"No changes made to {fname} (or pattern not found)")

