"""
Telegram bot: send it an .oab (Outlook Offline Address Book) file,
get back a .csv.

Setup:
    pip install "python-telegram-bot>=20"
    sudo apt install mspack-tools            # provides `oabextract` (needed for compressed OABs)
    export BOT_TOKEN="123456:ABC..."        # token from @BotFather
    python oab_bot.py
"""
import asyncio
import csv
import logging
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oab-bot")

TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Telegram Bot API limit for downloads

# Friendly names for common MAPI property IDs (high 16 bits of the tag)
PROP_NAMES = {
    0x3001: "DisplayName",
    0x3002: "AddressType",
    0x3003: "EmailAddress",
    0x3004: "Comment",
    0x3900: "DisplayType",
    0x39FE: "SmtpAddress",
    0x3A00: "Account",
    0x3A06: "GivenName",
    0x3A08: "BusinessPhone",
    0x3A09: "HomePhone",
    0x3A0A: "Initials",
    0x3A0F: "MessageHandlingSystemCommonName",
    0x3A11: "Surname",
    0x3A16: "Company",
    0x3A17: "Title",
    0x3A18: "Department",
    0x3A19: "OfficeLocation",
    0x3A1B: "BusinessPhone2",
    0x3A1C: "MobilePhone",
    0x3A21: "PagerNumber",
    0x3A23: "BusinessFax",
    0x3A26: "Country",
    0x3A27: "Locality",
    0x3A28: "State",
    0x3A29: "StreetAddress",
    0x3A2A: "PostalCode",
    0x3A2C: "TelexNumber",
    0x6800: "OrgUnit",
    0x8000: "ObjectGuid",
    0x800F: "ProxyAddresses",
    0x8C6A: "UserCertificate",
}


# --------------------------------------------------------------------------
# OAB parsing
# --------------------------------------------------------------------------
class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.p = data, pos

    def u8(self) -> int:
        v = self.d[self.p]
        self.p += 1
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def take(self, n: int) -> bytes:
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def varint(self) -> int:
        """OAB variable-length integer: <=0x7F is the value itself,
        0x8N means N little-endian bytes follow."""
        b = self.u8()
        if b <= 0x7F:
            return b
        return int.from_bytes(self.take(b & 0x0F), "little")

    def cstr(self) -> str:
        end = self.d.index(b"\x00", self.p)
        s = self.d[self.p:end].decode("utf-8", "replace")
        self.p = end + 1
        return s


def _read_single(r: Reader, base_type: int):
    if base_type in (0x1F, 0x1E):  # string / string8
        return r.cstr()
    if base_type == 0x0B:  # boolean
        return bool(r.u8())
    if base_type == 0x03:  # int32
        return r.varint()
    if base_type == 0x102:  # binary
        return r.take(r.varint()).hex()
    raise ValueError(f"Unsupported property type 0x{base_type:X}")


def _read_value(r: Reader, tag: int):
    ptype = tag & 0xFFFF
    base = ptype & 0x0FFF
    if ptype & 0x1000:  # multi-valued
        return ";".join(str(_read_single(r, base)) for _ in range(r.varint()))
    return _read_single(r, base)


def _read_record(r: Reader, attrs: list[int]) -> dict:
    start = r.p
    size = r.u32()
    end = start + size
    presence = r.take((len(attrs) + 7) // 8)
    row = {}
    for i, tag in enumerate(attrs):
        if presence[i // 8] & (0x80 >> (i % 8)):
            row[tag] = _read_value(r, tag)
    r.p = end  # always resync to the record boundary
    return row


def _col_name(tag: int) -> str:
    return PROP_NAMES.get(tag >> 16, f"0x{tag:08X}")


def parse_oab_details(data: bytes):
    """Parse a decompressed OAB v4 details file -> (columns, rows)."""
    r = Reader(data)
    version = r.u32()
    if version != 0x20:
        raise ValueError(f"Unsupported OAB version 0x{version:X} (expected v4 details file)")
    r.u32()  # serial
    total = r.u32()

    meta_start = r.p
    meta_size = r.u32()
    hdr_attrs = [(r.u32(), r.u32())[0] for _ in range(r.u32())]
    oab_attrs = [(r.u32(), r.u32())[0] for _ in range(r.u32())]
    r.p = meta_start + meta_size

    _read_record(r, hdr_attrs)  # header record (skipped)

    rows = []
    for _ in range(total):
        if r.p >= len(data):
            break
        rows.append(_read_record(r, oab_attrs))
    return oab_attrs, rows


def decompress_if_needed(src: Path, workdir: Path) -> bytes:
    raw = src.read_bytes()
    if len(raw) < 8:
        raise ValueError("File is too small to be an OAB file")
    hi, lo = struct.unpack_from("<II", raw, 0)
    if hi == 0x20:  # already a plain v4 details file
        return raw
    if hi == 3 and lo == 1:  # MSCompressedOAB (LZX)
        if not shutil.which("oabextract"):
            raise RuntimeError("`oabextract` not found. Install mspack-tools (apt) or libmspack (brew).")
        out = workdir / "decompressed.bin"
        subprocess.run(["oabextract", str(src), str(out)], check=True, capture_output=True)
        return out.read_bytes()
    raise ValueError("Unrecognised OAB format (browse/patch files are not supported)")


def oab_to_csv(src: Path, dst: Path) -> int:
    data = decompress_if_needed(src, dst.parent)
    attrs, rows = parse_oab_details(data)
    columns = []
    for t in attrs:  # keep order, drop duplicates
        if t not in columns:
            columns.append(t)
    with dst.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.writer(f)
        w.writerow([_col_name(t) for t in columns])
        for row in rows:
            w.writerow([row.get(t, "") for t in columns])
    return len(rows)


# --------------------------------------------------------------------------
# Telegram handlers
# --------------------------------------------------------------------------
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me an .oab file and I'll convert it to CSV.")


async def handle_document(update: Update, _: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    name = doc.file_name or "file"

    if not name.lower().endswith(".oab"):
        await update.message.reply_text("Please send a file with the .oab extension.")
        return
    if doc.file_size and doc.file_size > TG_DOWNLOAD_LIMIT:
        await update.message.reply_text("That file is over Telegram's 20 MB bot download limit.")
        return

    status = await update.message.reply_text("Converting…")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "input.oab"
        dst = tmp / (Path(name).stem + ".csv")
        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(src)
            count = await asyncio.to_thread(oab_to_csv, src, dst)
            with dst.open("rb") as f:
                await update.message.reply_document(
                    f, filename=dst.name, caption=f"Done: {count} records."
                )
            await status.delete()
        except Exception as e:  # noqa: BLE001
            log.exception("Conversion failed")
            await status.edit_text(f"Conversion failed: {e}")


def main():
    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    log.info("Bot running…")
    app.run_polling()


if __name__ == "__main__":
    main()
