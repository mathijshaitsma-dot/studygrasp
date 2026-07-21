from pathlib import Path
from supabase_client import supabase

BUCKET = "slides"

local_file = Path("testafbeelding.webp")
storage_path = "uploads/test-presentation/slide_001.png"

with open(local_file, "rb") as f:
    result = supabase.storage.from_(BUCKET).upload(
        path=storage_path,
        file=f,
        file_options={"content-type": "image/png"}
    )

print("Upload result:", result)

public_url_data = supabase.storage.from_(BUCKET).get_public_url(storage_path)
print("Public URL:", public_url_data)