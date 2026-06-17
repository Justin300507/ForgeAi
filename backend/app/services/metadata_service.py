import json
import os
from datetime import datetime


def save_metadata(
    project_path,
    plan,
    provider,
    generation_time
):

    metadata = {
        "project_name": plan["project_name"],
        "description": plan["description"],
        "provider": provider,
        "generated_at": datetime.now().isoformat(),
        "generation_time_seconds": generation_time,
        "version": "0.9"
    }

    metadata_path = os.path.join(
        project_path,
        "metadata.json"
    )

    with open(
        metadata_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            metadata,
            f,
            indent=4
        )

    return metadata_path