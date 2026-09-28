#!/usr/bin/env python3
"""Create mock data files from CLI output for napalm-fsos tests."""

import json
import os
import re

CLI_OUTPUT_FILE = "scripts/fsos_cli_output.txt"
MOCK_BASE = "test/unit/fsos/mock_data"


def extract_sections(filepath):
    """Extract command sections from the CLI output file."""
    sections = {}
    with open(filepath) as f:
        content = f.read()

    # Find all command markers
    pattern = r"={80}\n# (.+?)\n={80}\n(.*?)(?=={80}\n#|$)"
    matches = re.findall(pattern, content, re.DOTALL)

    for cmd, output in matches:
        # Clean up the output - remove the command echo at the start
        lines = output.strip().split("\n")
        if lines and lines[0].strip() == cmd:
            lines = lines[1:]
        # Remove trailing FS# prompt
        while lines and lines[-1].strip() == "FS#":
            lines.pop()
        sections[cmd.strip()] = "\n".join(lines).strip()

    return sections


def sanitize_command(cmd):
    """Convert command to filename-safe string."""
    return re.sub(r"[^a-zA-Z0-9]", "_", cmd).strip("_")


def create_mock_file(test_name, test_case, command, output):
    """Create a mock data file."""
    filename = sanitize_command(command)
    dirpath = os.path.join(MOCK_BASE, test_name, test_case)
    os.makedirs(dirpath, exist_ok=True)

    # Write as text file (the FakeFsosDevice reads .txt files for text encoding)
    filepath = os.path.join(dirpath, f"{filename}.txt")
    with open(filepath, "w") as f:
        f.write(output)
    print(f"  Created: {filepath} ({len(output)} chars)")


def create_expected_result(test_name, test_case, data):
    """Create an expected_result.json file."""
    dirpath = os.path.join(MOCK_BASE, test_name, test_case)
    os.makedirs(dirpath, exist_ok=True)
    filepath = os.path.join(dirpath, "expected_result.json")
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Created: {filepath}")


def main():
    sections = extract_sections(CLI_OUTPUT_FILE)

    print(f"Found {len(sections)} command sections")
    for cmd in sections:
        print(f"  {cmd}: {len(sections[cmd])} chars")

    # Map test methods to commands they call
    test_commands = {
        "test_get_facts": ["show version"],
        "test_get_interfaces": ["show interfaces", "show interfaces status"],
        "test_get_interfaces_ip": ["show interfaces"],
        "test_get_interfaces_counters": ["show interfaces"],
        "test_get_vlans": ["show vlan"],
        "test_get_lldp_neighbors": ["show lldp neighbors"],
        "test_get_lldp_neighbors_detail": ["show lldp neighbors detail"],
        "test_get_arp_table": ["show arp"],
        "test_get_config": ["show running-config"],
        "test_get_snmp_information": ["show snmp"],
        "test_get_users": ["show running-config"],
        "test_get_environment": ["show processes cpu"],
        "test_get_bgp_neighbors": ["show bgp summary"],
        "test_get_ipv6_neighbors_table": ["show ipv6 neighbors"],
        "test_get_ntp_peers": ["show ntp peers"],
        "test_get_ntp_servers": ["show ntp servers"],
        "test_get_ntp_stats": ["show ntp stats"],
        "test_get_mac_address_table": ["show mac address-table"],
        "test_get_route_to": ["show ip route"],
        "test_ping": ["ping 10.208.1.1 count 5 size 100 timeout 5"],
        "test_traceroute": ["traceroute 10.208.1.1 ttl 255 wait 5"],
        "test_is_alive": [],  # No commands, just checks socket
        "test_get_bgp_config": ["show bgp summary"],
        "test_get_probes_config": ["show probes config"],
        "test_get_probes_results": ["show probes results"],
        "test_get_optics": ["show interfaces transceiver"],
        "test_get_network_instances": ["show network-instance"],
        "test_get_firewall_policies": ["show firewall policies"],
    }

    for test_name, commands in test_commands.items():
        print(f"\n{test_name}:")
        for cmd in commands:
            if cmd in sections:
                create_mock_file(test_name, "default", cmd, sections[cmd])
            else:
                # Create empty output for commands not in CLI output
                create_mock_file(test_name, "default", cmd, "")
                print(f"  WARNING: No output for '{cmd}', created empty file")


if __name__ == "__main__":
    main()
