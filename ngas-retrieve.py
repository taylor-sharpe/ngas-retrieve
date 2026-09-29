import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

def quote_sftp(value: str) -> str:
    """Quote a path as one argument in an SFTP batch command."""
    if "\n" in value or "\r" in value:
        raise ValueError("SFTP paths cannot contain line breaks.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_read_only_commands(commands: list[str]):
    """Reject every SFTP batch operation except navigation, listing, and get."""
    allowed_operations = {"cd", "get"}
    for command in commands:
        if any(character in command for character in "\r\n"):
            raise ValueError("SFTP commands cannot contain line breaks.")

        operation = command.split(maxsplit=1)[0] if command else ""
        if operation == "ls" and command == "ls -l":
            continue
        if operation not in allowed_operations:
            raise ValueError(f"Blocked non-read-only SFTP command: {operation!r}")


def run_sftp_batch(server: str, commands: list[str], auth_method: str) -> str:
    """Runs read-only SFTP commands using the selected authentication method."""
    if not server or server.startswith("-"):
        raise ValueError("The SFTP server must be a non-empty destination, not an option.")
    validate_read_only_commands(commands)

    # Keep authentication settings explicit so password mode cannot fall back to GSSAPI.
    if auth_method == "id_card":
        auth_options = [
            "-o", "GSSAPIAuthentication=yes",
            "-o", "GSSAPIDelegateCredentials=no",
        ]
    elif auth_method == "password":
        auth_options = [
            "-o", "GSSAPIAuthentication=no",
            "-o", "PasswordAuthentication=yes",
            "-o", "KbdInteractiveAuthentication=yes",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
        ]
    else:
        raise ValueError(f"Unknown authentication method: {auth_method!r}")

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".sftp", delete=False
    ) as batch_file:
        batch_file.write("\n".join(commands) + "\n")
        batch_path = Path(batch_file.name)

    sftp_cmd = ["sftp", *auth_options, "-b", str(batch_path), server]
    try:
        result = subprocess.run(
            sftp_cmd,
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(
            f"\n❌ SFTP operation failed (exit code {e.returncode}).",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        batch_path.unlink(missing_ok=True)


def run_with_activity_bar(label: str, operation):
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(operation)
        if not sys.stdout.isatty():
            print(f"{label}...", flush=True)
            return result.result()

        frames = "|/-\\"
        started_at = time.monotonic()
        frame = 0
        try:
            while not result.done():
                elapsed = time.monotonic() - started_at
                print(
                    f"\r{label} [{frames[frame % len(frames)]}] {elapsed:.1f}s",
                    end="",
                    flush=True,
                )
                frame += 1
                time.sleep(0.1)
            return result.result()
        finally:
            print("\r" + " " * 100 + "\r", end="", flush=True)


def format_bytes(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024


def render_download_progress(downloaded: int, total: int, finished: bool = False):
    width = 30
    fraction = min(downloaded / total, 1) if total else 0
    if not finished:
        fraction = min(fraction, 0.999)
    filled = int(width * fraction)
    bar = "=" * filled + " " * (width - filled)
    message = (
        f"Downloading [{bar}] {fraction:>6.1%} "
        f"{format_bytes(downloaded)} / {format_bytes(total)}"
    )
    if sys.stdout.isatty():
        print(f"\r{message}", end="", flush=True)


def list_remote_files(server: str, remote_dir: str, auth_method: str) -> list[dict]:
    """Lists files and folders in the remote directory with sizes (read-only)."""
    output = run_with_activity_bar(
        "Fetching file list from server",
        lambda: run_sftp_batch(
            server,
            [f"cd {quote_sftp(remote_dir)}", "ls -l"],
            auth_method,
        ),
    )

    files = []
    lines = output.splitlines()

    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) >= 9 and not line.startswith("total"):
            permissions = parts[0]
            size = parts[4]
            name = parts[8]

            # Skip current and parent directory pointers
            if name in (".", ".."):
                continue

            is_dir = permissions.startswith("d")
            files.append({"name": name, "size": size, "is_dir": is_dir})

    return files


def parse_requested_date(value: str):
    for date_format in ("%Y-%m-%d", "%Y_%m_%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise ValueError("Enter a valid date as YYYY-MM-DD, YYYY_MM_DD, or YYYYMMDD.")


def parse_date_filter(value: str):
    tokens = value.split()
    if not 1 <= len(tokens) <= 3:
        raise ValueError("Enter one date, or a start date and end date, with an optional instrument number.")

    start_date = parse_requested_date(tokens[0])
    end_date = start_date
    instrument_number = None
    is_range = False

    if len(tokens) == 2:
        try:
            end_date = parse_requested_date(tokens[1])
            is_range = True
        except ValueError:
            if re.fullmatch(r"\d{4}", tokens[1]):
                instrument_number = tokens[1]
            else:
                raise ValueError("The second value must be an end date or a four-digit instrument number.")
    elif len(tokens) == 3:
        end_date = parse_requested_date(tokens[1])
        instrument_number = tokens[2]
        is_range = True

    if instrument_number and not re.fullmatch(r"\d{4}", instrument_number):
        raise ValueError("Instrument number must contain exactly four digits.")
    if end_date < start_date:
        raise ValueError("The end date must be the same as or later than the start date.")

    end_input = tokens[1] if is_range else tokens[0]
    return start_date, end_date, instrument_number, tokens[0], end_input, is_range


def filename_dates(filename: str, instrument_number: str | None = None):
    date_token = r"(?:\d{4}_\d{2}_\d{2}|\d{4}-\d{2}-\d{2}|\d{8})"
    patterns = [(rf"(?<!\d)({date_token})(?!\d)", False)]
    if instrument_number:
        patterns.append(
            (rf"(?<!\d)({date_token}){re.escape(instrument_number)}(?!\d)", True)
        )

    dates = []
    for pattern, has_concatenated_instrument in patterns:
        for match in re.finditer(pattern, filename):
            value = match.group(1)
            for date_format in ("%Y-%m-%d", "%Y_%m_%d", "%Y%m%d"):
                try:
                    matched_date = datetime.strptime(value, date_format).date()
                    dates.append((matched_date, has_concatenated_instrument))
                    break
                except ValueError:
                    continue
    return dates


def filename_matches_date(filename: str, requested_date) -> bool:
    return any(matched_date == requested_date for matched_date, _ in filename_dates(filename))


def filename_matches_instrument(filename: str, instrument_number: str) -> bool:
    instrument_pattern = rf"(?<!\d){re.escape(instrument_number)}(?!\d)"
    return re.search(instrument_pattern, filename) is not None


def filename_matches_date_and_instrument(
    filename: str, requested_date, instrument_number: str
) -> bool:
    if filename_matches_instrument(filename, instrument_number):
        return filename_matches_date(filename, requested_date)
    return any(
        matched_date == requested_date and has_concatenated_instrument
        for matched_date, has_concatenated_instrument in filename_dates(
            filename, instrument_number
        )
    )


def filename_matches_date_range(
    filename: str, start_date, end_date, instrument_number: str | None = None
) -> bool:
    standalone_instrument_match = (
        not instrument_number or filename_matches_instrument(filename, instrument_number)
    )
    return any(
        start_date <= matched_date <= end_date
        and (standalone_instrument_match or has_concatenated_instrument)
        for matched_date, has_concatenated_instrument in filename_dates(
            filename, instrument_number
        )
    )


def download_files(
    server: str,
    remote_dir: str,
    files: list[dict],
    local_dest: str,
    auth_method: str,
):
    """Downloads matching files using only SFTP get operations."""
    local_path = Path(local_dest).expanduser()
    local_path.mkdir(parents=True, exist_ok=True)

    downloads = []
    commands = [f"cd {quote_sftp(remote_dir)}"]
    for item in files:
        remote_file_path = f"./{item['name']}"
        local_file_path = local_path / item["name"]
        # Download beside the final file, then replace it only after the transfer succeeds.
        with tempfile.NamedTemporaryFile(
            prefix=".ngas-download-", suffix=".part", dir=local_path, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        temporary_path.unlink()
        try:
            expected_size = int(item["size"])
        except (KeyError, TypeError, ValueError):
            expected_size = 0
        downloads.append((temporary_path, local_file_path, expected_size))
        commands.append(
            f"get {quote_sftp(remote_file_path)} {quote_sftp(str(temporary_path))}"
        )

    total_size = sum(size for _, _, size in downloads)
    if not sys.stdout.isatty():
        print(f"Downloading {len(files)} file(s)...", flush=True)
    else:
        render_download_progress(0, total_size)

    with ThreadPoolExecutor(max_workers=1) as executor:
        transfer = executor.submit(run_sftp_batch, server, commands, auth_method)
        try:
            while not transfer.done():
                if sys.stdout.isatty():
                    downloaded = 0
                    for temporary_path, _, expected_size in downloads:
                        try:
                            current_size = temporary_path.stat().st_size
                        except FileNotFoundError:
                            current_size = 0
                        downloaded += min(current_size, expected_size)
                    render_download_progress(downloaded, total_size)
                time.sleep(0.1)
            transfer.result()
            for temporary_path, local_file_path, _ in downloads:
                os.replace(temporary_path, local_file_path)
        finally:
            for temporary_path, _, _ in downloads:
                temporary_path.unlink(missing_ok=True)

    if sys.stdout.isatty():
        render_download_progress(total_size, total_size, finished=True)
        print()
    print(f"✅ Downloaded {len(files)} file(s) to {local_path}\n")


def main():
    # --- CONFIGURATION DEFAULT DEFAULTS ---
    DEFAULT_SERVER = "sharpe@nimbus2.cmdl.noaa.gov"
    DEFAULT_USERNAME, SERVER_HOST = DEFAULT_SERVER.split("@", maxsplit=1)
    DEFAULT_REMOTE_DIR = "/isftp/sftp/data/ttea/ngas/incoming/ngas"
    DEFAULT_LOCAL_DIR = "~/Downloads"
    # -------------------------------------

    print("==========================================")
    print(" 🛠️  Interactive Remote File Downloader")
    print("==========================================")

    # Prompt for the username used with the configured server host
    username_input = input(f"Enter username, default:[{DEFAULT_USERNAME}]: ").strip()
    username = username_input if username_input else DEFAULT_USERNAME
    server = f"{username}@{SERVER_HOST}"

    print("\nChoose login method:")
    print("1. ID card (GSSAPI)")
    print("2. Password")
    auth_choice = input("Select method [1/2, default 1]: ").strip() or "1"
    if auth_choice not in ("1", "2"):
        print("❌ Invalid login method.")
        return
    auth_method = "id_card" if auth_choice == "1" else "password"

    # Get remote path
    print("\nTip: You can get this path on your server by running 'pwd'.")
    remote_dir_input = input(f"Enter remote directory path, default:[{DEFAULT_REMOTE_DIR}]: ").strip()
    remote_dir = remote_dir_input if remote_dir_input else DEFAULT_REMOTE_DIR

    print("\n🔍 Fetching file list from server...")
    files = list_remote_files(server, remote_dir, auth_method)

    if not files:
        print("📂 Directory is empty or path doesn't exist.")
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
        ) = parse_date_filter(date_filter_input)
    except ValueError as error:
        print(f"❌ {error}")
        return

    if instrument_number is None:
        instrument_number = input("Instrument number (optional, 4 digits): ").strip()
        if instrument_number and not re.fullmatch(r"\d{4}", instrument_number):
            print("❌ Instrument number must contain exactly four digits.")
            return
        instrument_number = instrument_number or None

    # Apply both filters to filenames; directories are never selected for download.
    matching_files = [
        item for item in files
        if not item["is_dir"]
        and filename_matches_date_range(
            item["name"], start_date, end_date, instrument_number
        )
    ]
    if not matching_files:
        search_description = start_date.isoformat()
        if is_range:
            search_description += f" through {end_date.isoformat()}"
        if instrument_number:
            search_description += f" and instrument {instrument_number}"
        print(f"📂 No files found for {search_description}.")
        return

    search_description = start_date.isoformat()
    if is_range:
        search_description += f" through {end_date.isoformat()}"
    if instrument_number:
        search_description += f" and instrument {instrument_number}"
    print(f"\nFiles matching {search_description}:")
    for item in matching_files:
        print(f"  {item['name']} ({item['size']})")

    # Destination prompt
    dest_input = input(f"Local download folder [{DEFAULT_LOCAL_DIR}]: ").strip()
    local_dest = dest_input if dest_input else DEFAULT_LOCAL_DIR

    folder_name = start_input
    if is_range:
        folder_name += f"_{end_input}"
    if instrument_number:
        folder_name += f"_{instrument_number}"
    date_dest = Path(local_dest).expanduser() / folder_name
    download_files(server, remote_dir, matching_files, str(date_dest), auth_method)


if __name__ == "__main__":
    main()