from process_pptx_upload import process_pptx
from get_slides import get_slides_for_presentation

presentation_id = process_pptx(r"C:\Users\mathi\Onedrive\Bureaublad\Study Copilot AI\Oefenpowerpoint.pptx")
slides = get_slides_for_presentation(presentation_id)

for slide in slides:
    print(slide["slide_number"], slide["public_url"])