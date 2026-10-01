import importlib.util
import re
import sys
from pathlib import Path


BASE_SCRIPT = Path(__file__).with_name("ngas-retrieve.py")


def load_downloader():
    spec = importlib.util.spec_from_file_location("ngas_retrieve", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load downloader module: {BASE_SCRIPT}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ngas = load_downloader()


# TODO: Fill these values only with credentials and destinations approved by your organization.
GOOGLE_DRIVE_FOLDER_ID = ""
GOOGLE_DRIVE_CREDENTIALS_PATH = ""


def upload_files_to_google_drive(
    local_folder: Path,
    drive_folder_id: str,
    credentials_path: str,
):
    """Upload completed local files to an approved Google Drive folder.

    Replace this function with the approved Google Drive API integration. The
    function is called only after all SFTP downloads complete successfully.
    It should raise an exception if any upload fails.
    """
    # TODO: Add the approved Google Drive client and authentication flow here.
    # TODO: Authenticate without hard-coding tokens, passwords, or secrets.
    # TODO: Create or select the destination folder using drive_folder_id.
    # TODO: Upload each file in local_folder and report per-file failures.
    raise NotImplementedError(
        "Google Drive upload is not configured. Fill in "
        "upload_files_to_google_drive() first."
    )


def main():
    default_server = "sharpe@nimbus2.cmdl.noaa.gov"
    default_username, server_host = default_server.split("@", maxsplit=1)
    default_remote_dir = "/isftp/sftp/data/ttea/ngas/incoming/ngas"
    default_local_dir = "~/Downloads"

    print("==========================================")
    print(" NGAS Retrieve + Google Drive")
    print("==========================================")

    username_input = input(f"Enter username, default:[{default_username}]: ").strip()
    username = username_input if username_input else default_username
    server = f"{username}@{server_host}"

    print("\nChoose login method:")
    print("1. ID card (GSSAPI)")
    print("2. Password")
    auth_choice = input("Select method [1/2, default 1]: ").strip() or "1"
    if auth_choice not in ("1", "2"):
        print("Invalid login method.")
        return
    auth_method = "id_card" if auth_choice == "1" else "password"

    remote_dir_input = input(
        f"Enter remote directory, default:[{default_remote_dir}]: "
    ).strip()
    remote_dir = remote_dir_input if remote_dir_input else default_remote_dir

    files = ngas.list_remote_files(server, remote_dir, auth_method)
    if not files:
        print("Remote directory is empty or does not exist.")
        return

    date_filter_input = input(
        "\nEnter a date or date range, optionally with a 4-digit instrument\n"
        "Example: 2026-09-25 2026-09-29 5686\n"
        "Date formats: YYYY-MM-DD, YYYY_MM_DD, or YYYYMMDD: "
    ).strip()
    try:
        (
            start_date,
            end_date,
            instrument_number,
            start_input,
            end_input,
            is_range,
        ) = ngas.parse_date_filter(date_filter_input)
    except ValueError as error:
        print(f"Invalid date filter: {error}")
        return

    if instrument_number is None:
        instrument_number = input("Instrument number (optional, 4 digits): ").strip()
        if instrument_number and not re.fullmatch(r"\d{4}", instrument_number):
            print("Instrument number must contain exactly four digits.")
            return
        instrument_number = instrument_number or None

    matching_files = [
        item
        for item in files
        if not item["is_dir"]
        and ngas.filename_matches_date_range(
            item["name"], start_date, end_date, instrument_number
        )
    ]
    if not matching_files:
        print("No matching files found.")
        return

    print(f"\nFound {len(matching_files)} matching file(s):")
    for item in matching_files:
        print(f"  {item['name']} ({item['size']})")

    local_dest_input = input(
        f"Local download folder [{default_local_dir}]: "
    ).strip()
    local_dest = local_dest_input if local_dest_input else default_local_dir

    folder_name = start_input
    if is_range:
        folder_name += f"_{end_input}"
    if instrument_number:
        folder_name += f"_{instrument_number}"
    date_dest = Path(local_dest).expanduser() / folder_name

    # The download completes before any cloud integration is attempted.
    ngas.download_files(
        server,
        remote_dir,
        matching_files,
        str(date_dest),
        auth_method,
    )

    try:
        upload_files_to_google_drive(
            date_dest,
            GOOGLE_DRIVE_FOLDER_ID,
            GOOGLE_DRIVE_CREDENTIALS_PATH,
        )
    except NotImplementedError as error:
        print(f"\nDownload complete; upload pending: {error}")
    except Exception as error:
        print(f"\nDownload complete, but Google Drive upload failed: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
