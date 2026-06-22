import json


def build_frontend_prompt(architecture):

    return f"""
You are ForgeAI Frontend Generator. Generate a high-quality, visually real React frontend.

Architecture:

{json.dumps(architecture, indent=2)}

Return ONLY valid JSON.

Format:

{{
  "files": [
    {{
      "path": "",
      "content": ""
    }}
  ]
}}

========================================
FILE RULES
========================================

- Generate every page and every component listed in frontend_structure
- Maximum 25 files total
- Generate only:
  - src/App.jsx
  - src/pages/*.jsx
  - src/components/*.jsx
- NEVER import a page or component you did not also generate in this same response.
  If you must cut scope, remove the import AND the JSX usage together.
- Every path must: start with src/, use forward slashes, contain only ASCII, end with .jsx

VALID:   src/App.jsx  |  src/pages/Login.jsx  |  src/components/Header.jsx
INVALID: src\\pages\\Login.jsx  |  src/pages/Login.js  |  src/pages/Login.tsx

If frontend_structure.components is empty:
- Do NOT create standalone reusable components
- Pages render their own JSX directly

========================================
UI QUALITY RULES — MANDATORY
========================================

Every generated component MUST look real and functional. No skeleton components.
No placeholder text. No empty divs.

Use inline styles for all visual design. No external CSS files. No Tailwind classes.

CONTAINER PATTERN — use this wrapper on every page:
  <div style={{{{margin:"0 auto",maxWidth:"900px",padding:"2rem",fontFamily:"sans-serif"}}}}>

LOGIN PAGE — MUST contain ALL of these:
  - <h1> or <h2> with the app name or "Sign In"
  - <input type="email" placeholder="Email" .../>
  - <input type="password" placeholder="Password" .../>
  - <button> with "Login" or "Sign In" text
  - All inputs must have value/onChange wired to useState

REGISTER PAGE — MUST contain ALL of these:
  - <h1> or <h2> with "Create Account" or "Sign Up"
  - <input> for name/username
  - <input type="email"> for email
  - <input type="password"> for password
  - <button> with "Register" or "Create Account" text
  - All inputs must have value/onChange wired to useState

DASHBOARD / HOME PAGE — MUST contain ALL of these:
  - A visible heading (h1 or h2) describing what the page shows
  - At least one interactive element (button, form, or clickable card)
  - A content area with real placeholder data (at least 2-3 items/cards)
  - Navigation links or a header bar

LIST / INDEX PAGES (TaskList, NoteList, etc.) — MUST contain:
  - A heading
  - A "New [Resource]" or "Add [Resource]" button
  - At least 2 hardcoded example items rendered in a card or list style
    (use useState with an initial array — do not leave the list empty)

FORM PAGES (Create, Edit) — MUST contain:
  - A heading
  - All relevant <input> or <textarea> fields for that resource
  - A submit <button>
  - All inputs wired to useState

CARD STYLE for list items (use this or similar):
  <div style={{{{border:"1px solid #ddd",borderRadius:"8px",padding:"1rem",marginBottom:"1rem",background:"#fff"}}}}>

BUTTON STYLE (use this or similar):
  <button style={{{{background:"#4f46e5",color:"#fff",border:"none",borderRadius:"6px",padding:"0.5rem 1.2rem",cursor:"pointer"}}}}>

HEADER / NAV pattern:
  <nav style={{{{background:"#1e1b4b",color:"#fff",padding:"1rem 2rem",display:"flex",justifyContent:"space-between",alignItems:"center"}}}}>

========================================
CODE RULES
========================================

- Use React.useState for local state in every interactive component
- Use arrow functions: const MyComponent = () => {{ ... }}
- Keep file length reasonable: aim for 40-120 lines per file
- No external libraries except React and react-router-dom
- No comments
- Import React at the top of every file: import React, {{ useState }} from 'react';
- Use Link from react-router-dom for navigation between pages

========================================
IMPORT RULES
========================================

- Import only generated files
- Every imported page must exist in this response
- Every imported component must exist in this response
- Every page and component MUST use: export default ComponentName
- Import default exports WITHOUT curly braces:
  Valid:   import LoginPage from './pages/Login';
  Invalid: import {{ LoginPage }} from './pages/Login';
- NEVER mix named-import syntax with a default export

========================================
APP.JSX RULES
========================================

- App.jsx MUST be generated
- Use BrowserRouter + Routes + Route from react-router-dom
- Wire every generated page to a route path
- Include a basic nav or redirect from "/" to the most logical landing page

========================================
JSON ESCAPE RULES
========================================

- Every file's content is a JSON string value
- Escape newlines as \\n
- Escape double quotes as \\"
- Escape backslashes as \\\\
- Only escape these characters: \\" \\\\ \\n \\t \\r
- NEVER put a backslash before any other character
- Do not output a raw, literal line break inside a JSON string

========================================
RELIABILITY RULES
========================================

- Prioritize valid JSON above all else
- If you must choose between feature richness and JSON validity, choose validity
- Test every import: if the target file is not in your response, remove the import
- Test every string: ensure every " inside JSX content is escaped as \\"

Before returning:

1. Verify all imports reference files you generated in this response.
2. Verify all paths are valid (src/*.jsx format).
3. Verify every import matches its target's export style (default vs named).
4. Verify JSON is valid — no raw newlines in string values.
5. Verify every Login/Register page has real input fields.
6. Verify every list page has at least 2 example items.
7. Verify every page has a visible heading.

Return JSON only.
"""
