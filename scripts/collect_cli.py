#!/usr/bin/env python3
"""Collect CLI output from FSOS switch using the napalm-fsos driver.

Usage:
    python collect_cli.py --host 10.208.1.13 --model S3410-48TS-P
    python collect_cli.py --host 10.208.1.13 --model S3410-48TS-P --username admin --password admin
    python collect_cli.py --host 10.208.1.13 --model S3410-48TS-P show version show interfaces
"""

import logging
import re
import os
import argparse

from napalm_fsos import FsosDriver

log = logging.getLogger(__name__)

DEFAULT_HOST = "10.208.1.13"
DEFAULT_PORT = 23
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_OUTPUT_DIR = "captured"

COMMANDS = [
    "show version",
    "show interfaces",
    "show interfaces status",
    "show vlan",
    "show mac",
    "show mac address-table learning",
    "show mac address-table static",
    "show lldp neighbors",
    "show lldp neighbors detail",
    "show lldp",
    "show ip route",
    "show ip route summary",
    "show ip route static",
    "show ip route connected",
    "show bgp summary",
    "show bgp neighbors",
    "show snmp",
    "show snmp community",
    "show snmp user",
    "show clock",
    "show inventory",
    "show environment",
    "show environment temperature",
    "show environment power",
    "show environment fan",
    "show fan",
    "show cpu",
    "show power",
    "show temperature",
    "show processes cpu",
    "show users",
    "show logging",
    "show arp",
    "show ip arp",
    "show ip neighbors",
    "show running-config",
    "show running-config interface",
    "show running-config vlan",
    "show running-config snmp",
    "show running-config interface Gi0/1",
    "show running-config interface Te0/49",
    "show memory",
    "ping 1.1.1.1 ntimes 5 length 100 timeout 2",
    "ping 2.2.2.2 ntimes 5 length 100 timeout 2",
    "traceroute ip 1.1.1.1 ttl 1 30 timeout 2",
    "traceroute ip 2.2.2.2 ttl 1 30 timeout 2",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect CLI output from FSOS switch using the napalm-fsos driver."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Switch hostname or IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Telnet port")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Login username")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Login password")
    parser.add_argument(
        "--model",
        default=None,
        help="Device model (used for output directory). "
        "If not provided, detected from 'show version' output.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Base directory for captured output (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "commands",
        nargs="*",
        help="Specific commands to run. If omitted, runs all default commands.",
    )
    return parser.parse_args()


def sanitize_command(cmd):
    """Convert a CLI command to a filename-safe string."""
    return re.sub(r"[^a-zA-Z0-9]", "_", cmd).strip("_")


def detect_model(version_output):
    """Extract the switch model from 'show version' output."""
    patterns = [
        r"Model\s+(\S+)",
        r"(S\d{4}-\d+[A-Z]*)",
        r"(S\d{4}-\d+\w*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, version_output, re.IGNORECASE)
        if match:
            return match.group(1)
    return "unknown"


def log(msg):
    print(msg, flush=True)


def save_command_output(model, cmd, output, data_dir):
    """Save a single command's output to data/{model}/{sanitized_command}.txt."""
    model_dir = os.path.join(data_dir, model)
    os.makedirs(model_dir, exist_ok=True)
    filename = sanitize_command(cmd) + ".txt"
    filepath = os.path.join(model_dir, filename)
    with open(filepath, "w") as f:
        f.write(output.rstrip("\n") + "\n")
    return filepath


def main():
    args = parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")

    # Determine commands to run
    commands = args.commands if args.commands else COMMANDS

    # Build optional args for the driver
    optional_args = {"port": args.port}

    # Create and open the driver
    log(f"Connecting to {args.host}:{args.port}...")
    driver = FsosDriver(
        hostname=args.host,
        username=args.username,
        password=args.password,
        timeout=30,
        optional_args=optional_args,
    )

    try:
        driver.open()
        log("Connected!")
    except Exception as e:
        log(f"Connection failed: {e}")
        return 1

    # Detect model from show version if not provided
    model = args.model
    version_output = None

    results = []
    for i, cmd in enumerate(commands, 1):
        log(f"\n[{i}/{len(commands)}] {cmd}")
        try:
            cli_output = driver.cli([cmd])
            result = cli_output.get(cmd, "")
            results.append((cmd, result))
            log(f"  Output length: {len(result)} chars")
            if result:
                log(f"  First 200 chars: {result[:200]}")

            # Detect model from show version output
            if cmd == "show version" and not model:
                version_output = result
                model = detect_model(result)
                log(f"  Detected model: {model}")

            # Save output to captured/{model}/{sanitized_command}.txt
            if model:
                filepath = save_command_output(model, cmd, result, args.output_dir)
                log(f"  Saved to: {filepath}")
            else:
                log(f"  WARNING: Model not yet detected, skipping save for '{cmd}'")

        except Exception as e:
            log(f"  ERROR: {e}")
            results.append((cmd, f"ERROR: {e}"))

    driver.close()
    log("\nConnection closed.")

    log(f"\nCaptured output saved to {args.output_dir}/{model}/")
    log(f"Total commands: {len(results)}")
    return 0


if __name__ == "__main__":
    exit(main())
