import email
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from pathlib import Path

from scripts.extract_eml_logs import extract_logs


def _make_eml(path: Path, attachments: dict[str, bytes], logo_bytes: bytes | None = None):
    msg = MIMEMultipart()
    msg['Subject'] = 'Cyclotron logs'
    for name, content in attachments.items():
        part = MIMEApplication(content, Name=name)
        part['Content-Disposition'] = f'attachment; filename="{name}"'
        msg.attach(part)
    if logo_bytes is not None:
        img = MIMEImage(logo_bytes, name='C2_signature_petlablogo_abc.png')
        img['Content-Disposition'] = 'inline; filename="C2_signature_petlablogo_abc.png"'
        msg.attach(img)
    path.write_text(msg.as_string())


def test_extract_logs_writes_unique_attachments(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    _make_eml(src / "a.eml", {"1.log": b"aaa", "2.log": b"bbb"})
    _make_eml(src / "b.eml", {"2.log": b"bbb", "3.log": b"ccc"})

    result = extract_logs([str(src / "a.eml"), str(src / "b.eml")], str(out))

    assert result['files_written'] == 3
    assert result['duplicates_skipped'] == 1
    assert (out / "1.log").read_bytes() == b"aaa"
    assert (out / "2.log").read_bytes() == b"bbb"
    assert (out / "3.log").read_bytes() == b"ccc"


def test_extract_logs_saves_logo_once(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    # Real PNG magic-number signature so email.mime.image.MIMEImage can guess
    # the subtype (b"PNGDATA" alone isn't a valid image and raises TypeError).
    logo_bytes = b"\x89PNG\r\n\x1a\nDATA"
    _make_eml(src / "a.eml", {"1.log": b"aaa"}, logo_bytes=logo_bytes)
    _make_eml(src / "b.eml", {"2.log": b"bbb"}, logo_bytes=logo_bytes)

    result = extract_logs([str(src / "a.eml"), str(src / "b.eml")], str(out))

    assert result['logo_saved'] is True
    assert (out / "petlab_logo.png").read_bytes() == logo_bytes


def test_extract_logs_skips_malformed_eml(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    (src / "broken.eml").write_bytes(b"\xff\xfe not a valid mime message")
    _make_eml(src / "a.eml", {"1.log": b"aaa"})

    result = extract_logs([str(src / "broken.eml"), str(src / "a.eml")], str(out))

    assert result['files_written'] == 1
