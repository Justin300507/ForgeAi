import sys
from app.services.project_service import generate_multiple_projects

def main():
    """
    Run the full ForgeAI generation pipeline for six sample ideas.
    The function will generate a separate FastAPI + React project for each
    idea, apply all validation and fix loops, and finally print a concise
    summary report.
    """
    ideas = [
        "A simple todo list app with user authentication",
        "A blog platform with markdown support and comments",
        "An e‑commerce store with product catalog and cart functionality",
        "A real‑time chat application using websockets",
        "A personal finance tracker with budgeting and reports",
        "A recipe manager with ingredient search and ratings"
    ]

    # Use the same provider that the rest of the pipeline expects.
    # "cerebras" is the default used elsewhere in the codebase.
    results = generate_multiple_projects(ideas, provider="cerebras")

    # ------------------------------------------------------------------
    # Print a concise summary of all generated projects.
    # ------------------------------------------------------------------
    print("\n=== SUMMARY OF ALL GENERATED PROJECTS ===")
    for project_name, data in results.items():
        if "error" in data:
            print(f"{project_name}: ❌ Generation failed – {data['error']}")
        else:
            print(
                f"{project_name}: ✅ Forge Score {data['forge_score']}, "
                f"Endpoint Coverage {data['endpoint_coverage']}, "
                f"Validation {'✔' if data['validation_passed'] else '✘'}, "
                f"Runtime {'✔' if data['runtime_passed'] else '✘'}"
            )

if __name__ == "__main__":
    # Allow the script to be run directly.
    main()
