import email
import os
import sys
from pathlib import Path


def extract_logs(eml_paths: list, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    seen = set()
    files_written = 0
    duplicates_skipped = 0
    logo_saved = False

    for eml_path in eml_paths:
        try:
            with open(eml_path, 'r', encoding='utf-8', errors='ignore') as f:
                msg = email.message_from_file(f)
        except Exception as e:
            print(f"  WARN {eml_path}: could not parse ({e}), skipping")
            continue

        for part in msg.walk():
            fn = part.get_filename()
            if not fn:
                continue
            payload = part.get_payload(decode=True) or b""

            if fn.startswith('C2_signature_petlablogo'):
                if not logo_saved:
                    with open(os.path.join(out_dir, 'petlab_logo.png'), 'wb') as out:
                        out.write(payload)
                    logo_saved = True
                continue

            if fn in seen:
                duplicates_skipped += 1
                continue
            seen.add(fn)
            with open(os.path.join(out_dir, fn), 'wb') as out:
                out.write(payload)
            files_written += 1

    return {
        'files_written': files_written,
        'duplicates_skipped': duplicates_skipped,
        'logo_saved': logo_saved,
    }


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python scripts/extract_eml_logs.py <src_dir_with_eml_files> <out_dir>")
        sys.exit(1)
    src_dir, out_dir = sys.argv[1], sys.argv[2]
    eml_files = [str(p) for p in Path(src_dir).glob('*.eml')]
    result = extract_logs(eml_files, out_dir)
    print(f"Wrote {result['files_written']} files "
          f"({result['duplicates_skipped']} duplicates skipped, "
          f"logo_saved={result['logo_saved']}) to {out_dir}")
