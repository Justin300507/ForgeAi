from app.services.fix_writer_service import write_fix


def write_runtime_fix(
    project_path,
    fix
):
    write_fix(
        project_path,
        fix
    )