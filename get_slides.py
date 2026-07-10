from supabase_client import supabase

def get_slides_for_presentation(presentation_id: str):
    result = (
        supabase.table("slides")
        .select("*")
        .eq("presentation_id", presentation_id)
        .order("slide_number")
        .execute()
    )
    return result.data