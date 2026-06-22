import os

project_path = r"C:\Users\jerry\oneDrive\Desktop\forgeAi\generated_projects\librarianpro"

routes_dir = os.path.join(
    project_path,
    "app",
    "routes"
)

for file in os.listdir(routes_dir):

    if not file.endswith(".py"):
        continue

    print("\n================")
    print(file)
    print("================")

    with open(
        os.path.join(routes_dir, file),
        "r",
        encoding="utf-8"
    ) as f:

        print(f.read()[:2000])