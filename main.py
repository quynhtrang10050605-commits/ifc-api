from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import base64, tempfile, os, math
import ifcopenshell

app = FastAPI(title="IFC Extractor API")

def shoelace_area(pts):
    n = len(pts)
    if n < 3:
        return None
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


def extract_ifc(ifc_path: str) -> list[dict]:
    model = ifcopenshell.open(ifc_path)
    schema = model.schema
    types = [
        "IfcBeam", "IfcColumn", "IfcPile", "IfcSlab",
        "IfcFooting", "IfcWall", "IfcWallStandardCase",
        "IfcPlate", "IfcMember", "IfcBuildingElementProxy",
    ]
    if schema.upper().startswith("IFC4"):
        types.append("IfcCivilElement")
    elements = []
    for t in types:
        elements.extend(model.by_type(t))
    material_map: dict[int, str] = {}
    for assoc in model.by_type("IfcRelAssociatesMaterial"):
        mat = assoc.RelatingMaterial
        if mat and assoc.RelatedObjects:
            name = getattr(mat, "Name", str(mat))
            for obj in assoc.RelatedObjects:
                material_map[obj.id()] = name
    rows = []
    for el in elements:
        row: dict = {
            "ifc_class": el.is_a(),
            "global_id": getattr(el, "GlobalId", None),
            "name": getattr(el, "Name", None),
            "material": material_map.get(el.id()),
            "extrusion_depth_m": None,
            "profile_type": None,
            "profile_width_m": None,
            "profile_height_m": None,
            "cross_section_area_m2": None,
            "volume_m3": None,
            "moment_of_inertia_Ix_m4": None,
            "moment_of_inertia_Iy_m4": None,
            "section_perimeter_m": None,
            "placement_x_m": None,
            "placement_y_m": None,
            "placement_z_m": None,
        }
        pl = getattr(el, "ObjectPlacement", None)
        if pl and pl.is_a("IfcLocalPlacement"):
            rel = pl.RelativePlacement
            if rel and rel.is_a("IfcAxis2Placement3D") and rel.Location:
                c = rel.Location.Coordinates
                if len(c) >= 3:
                    row["placement_x_m"] = c[0]
                    row["placement_y_m"] = c[1]
                    row["placement_z_m"] = c[2]
        if el.Representation:
            for sr in el.Representation.Representations:
                for item in sr.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        depth = getattr(item, "Depth", None)
                        row["extrusion_depth_m"] = depth
                        profile = item.SweptArea
                        if profile:
                            row["profile_type"] = profile.is_a()
                            if profile.is_a("IfcRectangleProfileDef"):
                                w = getattr(profile, "XDim", None)
                                h = getattr(profile, "YDim", None)
                                row["profile_width_m"] = w
                                row["profile_height_m"] = h
                                if w and h:
                                    row["cross_section_area_m2"] = round(w * h, 6)
                                    row["moment_of_inertia_Ix_m4"] = round(w * h**3 / 12, 9)
                                    row["moment_of_inertia_Iy_m4"] = round(h * w**3 / 12, 9)
                                    row["section_perimeter_m"] = round(2 * (w + h), 6)
                            elif profile.is_a("IfcCircleProfileDef"):
                                r = getattr(profile, "Radius", None)
                                if r:
                                    row["profile_width_m"] = round(r * 2, 6)
                                    row["profile_height_m"] = round(r * 2, 6)
                                    row["cross_section_area_m2"] = round(math.pi * r * r, 6)
                                    row["moment_of_inertia_Ix_m4"] = round(math.pi * r**4 / 4, 9)
                                    row["moment_of_inertia_Iy_m4"] = round(math.pi * r**4 / 4, 9)
                                    row["section_perimeter_m"] = round(2 * math.pi * r, 6)
                            elif profile.is_a("IfcArbitraryClosedProfileDef"):
                                curve = profile.OuterCurve
                                if curve and curve.is_a("IfcPolyline"):
                                    pts = [p.Coordinates for p in curve.Points]
                                    if pts:
                                        xs = [p[0] for p in pts]
                                        ys = [p[1] for p in pts]
                                        row["profile_width_m"] = round(max(xs) - min(xs), 6)
                                        row["profile_height_m"] = round(max(ys) - min(ys), 6)
                                        area = shoelace_area(pts)
                                        if area:
                                            row["cross_section_area_m2"] = round(area, 6)
                            a = row.get("cross_section_area_m2")
                            if a and depth:
                                row["volume_m3"] = round(a * depth, 6)
        rows.append(row)
    return rows


class ExtractRequest(BaseModel):
    file_content_base64: str
    file_name: str = "upload.ifc"


class ExtractResponse(BaseModel):
    success: bool
    count: int
    rows: list[dict]
    error: str | None = None


@app.get("/")
def health():
    return {"status": "ok", "service": "IFC Extractor API"}


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest):
    try:
        # Clean base64 string
        b64 = req.file_content_base64
        # Remove data URL prefix if present
        if ',' in b64:
            b64 = b64.split(',')[1]
        # Remove whitespace
        b64 = b64.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        # Fix padding
        missing = len(b64) % 4
        if missing:
            b64 += '=' * (4 - missing)
        file_bytes = base64.b64decode(b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {str(e)}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        rows = extract_ifc(tmp_path)
        return ExtractResponse(success=True, count=len(rows), rows=rows)
    except Exception as exc:
        return ExtractResponse(success=False, count=0, rows=[], error=str(exc))
    finally:
        os.remove(tmp_path)
