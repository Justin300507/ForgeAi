def build_frontend_prompt(architecture):

    return f"""
You are a senior React frontend engineer.

Given this architecture:

{architecture}

Return ONLY valid JSON.

Generate a runnable React frontend.

Required files:

- src/App.jsx

For each page generate:

- pages/<Page>.jsx

For each component generate:

- components/<Component>.jsx

Format:

{{
  "files": [
    {{
      "path": "",
      "content": ""
    }}
  ]
}}

CRITICAL RULES:

- Return JSON only
- No markdown
- No explanations
- No code fences

- Maximum 8 files

- Generate runnable React code

- Every imported page MUST exist
- Every imported component MUST exist

- Do NOT import files that are not generated

- All import paths must match generated files

- App.jsx must only import generated pages and components

- Components must only import generated components

- Avoid placeholder imports

- Keep files concise
- Keep files under 50 lines where possible

EXAMPLE:

If App.jsx contains:

import Home from './pages/Home'

Then Home.jsx MUST be generated.

If Home.jsx contains:

import ProductCard from '../components/ProductCard'

Then ProductCard.jsx MUST be generated.

Generate production-style but minimal runnable code.
"""