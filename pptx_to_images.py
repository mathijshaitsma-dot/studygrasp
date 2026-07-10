import os
from pathlib import Path
import pythoncom
import win32com.client


def export_pptx_to_pngs(pptx_path: str, output_dir: str):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    powerpoint = None
    presentation = None

    try:
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        powerpoint.Visible = 1

        presentation = powerpoint.Presentations.Open(
            str(Path(pptx_path).resolve()),
            False,
            False,
            False
        )

        presentation.SaveAs(str(output_path.resolve()), 18)  # 18 = PNG
        presentation.Close()
        powerpoint.Quit()

    finally:
        try:
            if presentation:
                presentation.Close()
        except:
            pass

        try:
            if powerpoint:
                powerpoint.Quit()
        except:
            pass