@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest):
    try:
        # Thêm padding nếu thiếu
        b64 = req.file_content_base64
        # Xóa prefix data URL nếu có
        if ',' in b64:
            b64 = b64.split(',')[1]
        # Thêm padding
        b64 += '=' * (4 - len(b64) % 4) if len(b64) % 4 else ''
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
