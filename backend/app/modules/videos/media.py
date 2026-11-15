from fastapi import HTTPException


def byte_range(header, size):
    if header is None:
        return None
    try:
        if not header.startswith("bytes=") or "," in header or len(header) > 128:
            raise ValueError
        start, end = header[6:].split("-")
        if not start:
            if not end.isascii() or not end.isdecimal() or int(end) <= 0:
                raise ValueError
            return max(0, size - int(end)), size - 1
        if (
            not start.isascii()
            or not start.isdecimal()
            or (end and (not end.isascii() or not end.isdecimal()))
        ):
            raise ValueError
        first, last = int(start), int(end) if end else size - 1
        if first >= size or last < first:
            raise ValueError
        return first, min(last, size - 1)
    except (ValueError, OverflowError):
        raise HTTPException(
            416, "Недопустимый Range", headers={"Content-Range": f"bytes */{size}"}
        ) from None
