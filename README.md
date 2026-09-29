# NGAS Retrieve

An interactive command-line tool for finding and downloading NGAS files from a remote SFTP server.

## Requirements

- Python 3.10 or newer
- OpenSSH `sftp` available on `PATH`
- Network access to the server and a valid ID-card/GSSAPI or password login

No third-party Python packages are required.

## Run

From this folder, start PowerShell or another terminal and run:

```powershell
python .\ngas-retrieve.py
```

The program prompts for the server, login method, and remote directory. Press Enter to use each configured default. The defaults can be changed in the configuration section near the start of `main()`.

## Find Files

Enter one date or an inclusive start and end date. Supported date formats are `YYYY-MM-DD`, `YYYY_MM_DD`, and `YYYYMMDD`.

Examples:

```text
2026-09-29
2026-09-29 5696
2026-09-25 2026-09-29 5686
```

The instrument number is optional and must be four digits. You can include it on the date line as shown, or enter it at the follow-up prompt. Without an instrument number, all files matching the date or date range are selected. Date ranges include both endpoints.

## Downloads

Matching files are saved beneath the local download directory (default `~/Downloads`) in a folder named for the date filter. For example:

```text
Downloads/2026-09-29_5696/
Downloads/2026-09-25_2026-09-29_5686/
```

The tool shows activity while listing the remote directory and download progress when run in a terminal. If a downloaded filename already exists in the destination folder, the successful download replaces it; a failed transfer leaves the existing file in place.

## Remote Access

The tool uses OpenSSH SFTP. ID-card login enables GSSAPI authentication without credential delegation; password login lets OpenSSH prompt for a password or keyboard-interactive response. Passwords are not stored by the script.

Remote SFTP commands are limited to directory navigation, listing, and file retrieval (`cd`, `ls`, and `get`). Downloads are written locally. The server may still record connection or file-access logs, and actual read-only access ultimately depends on server permissions.