import argparse
from pathlib import Path

from google.cloud import storage

BUCKET_NAME = "autotest-screenshot"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a local file to a Google Cloud Storage bucket."
    )
    parser.add_argument("file", type=Path, help="Local file to upload")
    parser.add_argument(
        "--object-name",
        help="Destination object name (defaults to the local file name)",
    )
    args = parser.parse_args()

    if BUCKET_NAME == "YOUR_BUCKET_NAME":
        parser.error("Set BUCKET_NAME in this script to your existing GCS bucket.")

    if not args.file.is_file():
        parser.error(f"File does not exist: {args.file}")

    object_name = args.object_name or args.file.name
    blob = storage.Client().bucket(BUCKET_NAME).blob(object_name)
    blob.upload_from_filename(str(args.file))

    print(f"Uploaded {args.file} to gs://{BUCKET_NAME}/{object_name}")


if __name__ == "__main__":
    main()
