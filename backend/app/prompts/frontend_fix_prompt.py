def build_frontend_fix_prompt(file_path: str, current_content: str, build_errors: list[str]) -> str:
    errors_block = "\n".join(f"  - {e}" for e in build_errors)
    return f"""You are a React/JSX build error fixer.

FILE: {file_path}

BUILD ERRORS:
{errors_block}

CURRENT FILE CONTENT:
{current_content}

Fix every build error listed above. Return ONLY the corrected file content.

RULES:
- Return raw JSX/JS code only — no markdown, no code fences, no explanations
- Keep every import that is valid; remove or fix imports that cause errors
- Every component must use: export default ComponentName
- Import React and useState at the top if JSX/state is used
- Never add external libraries other than react, react-dom, react-router-dom, axios
- Fix "is not defined" errors by adding the import or removing the reference
- Fix "Cannot find module" errors by correcting the import path or removing the import
- Fix SyntaxErrors by correcting the JSX syntax
- Do not change logic that is not related to the errors

UI QUALITY — maintain these while fixing:
- If this is a Login/auth page, it MUST keep: email input, password input, submit button
- If this is a Register/signup page, it MUST keep: name input, email input, password input, submit button
- If this is a Dashboard/home page, it MUST keep: a heading, some content cards or data display
- If this is a list page, it MUST keep: a heading, a list/table of items
- Do NOT simplify or hollow out working UI while fixing import/syntax errors
- Use inline styles for any styling — no CSS files, no Tailwind
"""
