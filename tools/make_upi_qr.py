"""Generate the committed UPI QR from the VPA set in web/static/common.js.

A VPA never changes, so the QR is the same bytes forever -- generating it per request, or
shipping a QR library to every visitor's browser, would be work repeated indefinitely to
produce a constant. It is a build-time artefact, committed like any other image.

Run after setting UPI_ID:

    uv run --with "qrcode[pil]" python tools/make_upi_qr.py

`qrcode` is deliberately NOT a project dependency -- this runs once when the VPA is set or
changed, the same way Pillow was used transiently for the image conversion.
"""

from __future__ import annotations

import pathlib
import re
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMMON = ROOT / "web" / "static" / "common.js"
OUT = ROOT / "web" / "static" / "upi-qr.png"


def _const(name: str) -> str:
    m = re.search(rf"^const {name} = '([^']*)';", COMMON.read_text(), re.M)
    if m is None:
        raise SystemExit(f"could not find `const {name}` in {COMMON}")
    return m.group(1)


def main() -> None:
    vpa = _const("UPI_ID")
    if not vpa:
        raise SystemExit(
            "UPI_ID is empty in web/static/common.js -- set it to a real VPA first.\n"
            "Nothing is generated for a blank id on purpose: a QR encoding a placeholder\n"
            "is worse than no QR, because it looks payable."
        )
    payee = _const("UPI_PAYEE")
    # No `am=`: this is a tip, so the payer sets the amount. Fixing one would also mean one
    # QR per amount, which is why the modal offers a single code.
    uri = f"upi://pay?pa={quote(vpa)}&pn={quote(payee)}&cu=INR"

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    img = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=2)
    img.add_data(uri)
    img.make(fit=True)
    # Dark-on-light: a UPI QR has to survive being photographed off a screen by another
    # phone, and inverting it for the dark theme costs scan reliability for styling.
    img.make_image(fill_color="#0a0f1c", back_color="#ffffff").save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)} for {vpa}")
    print(f"  encodes: {uri}")


if __name__ == "__main__":
    sys.exit(main())
