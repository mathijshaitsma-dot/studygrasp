import os
from pathlib import Path
from uuid import uuid4

from supabase_client import supabase
from pptx_to_images import export_pptx_to_pngs

BUCKET = "slides"


def natural_sort_key(filename: str):
    import re
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", filename)]


def process_pptx(file_path: str):
    original_filename = Path(file_path).name

    # 1. presentatie record maken
    pres_insert = supabase.table("presentations").insert({
        "original_filename": original_filename
    }).execute()

    presentation = pres_insert.data[0]
    presentation_id = presentation["id"]

    # 2. exportmap
    output_dir = Path("temp_exports") / presentation_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 3. pptx -> png
    export_pptx_to_pngs(file_path, str(output_dir))

    # 4. alle png's uploaden
    png_files = sorted(
        [f for f in os.listdir(output_dir) if f.lower().endswith(".png")],
        key=natural_sort_key
    )

    for index, filename in enumerate(png_files, start=1):
        local_path = output_dir / filename
        storage_path = f"uploads/{presentation_id}/slide_{index:03}.png"

        with open(local_path, "rb") as f:
            supabase.storage.from_(BUCKET).upload(
                path=storage_path,
                file=f,
                file_options={"content-type": "image/png"}
            )

        public_url = supabase.storage.from_(BUCKET).get_public_url(storage_path)

        supabase.table("slides").insert({
            "presentation_id": presentation_id,
            "slide_number": index,
            "storage_path": storage_path,
            "public_url": public_url
        }).execute()

    return presentation_id