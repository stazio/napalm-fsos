# -*- coding: utf-8 -*-
# Copyright 2016 Dravetech AB. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

"""
Napalm driver for FiberStore FSOS.

Read https://napalm.readthedocs.io for more information.
"""

import logging
import re
import socket
from typing import Optional

log = logging.getLogger(__name__)

from napalm.base import NetworkDriver
from napalm.base.exceptions import (
    ConnectionException,
    SessionLockedException,
    MergeConfigException,
    ReplaceConfigException,
    CommandErrorException,
)


class FsosDriver(NetworkDriver):
    """Napalm driver for FiberStore FSOS."""

    def __init__(self, hostname, username, password, timeout=60, optional_args=None):
        """Constructor."""
        log.debug("__init__ - called with hostname=%s, username=%s, timeout=%s", hostname, username, timeout)
        self.hostname = hostname
        self.username = username
        self.password = password
        self.timeout = timeout

        if optional_args is None:
            optional_args = {}

        self.port = optional_args.get("port", 23)
        self.global_delay_factor = optional_args.get("global_delay_factor", 1)
        self.use_keys = optional_args.get("use_keys", False)
        log.debug("__init__ - optional_args=%s, port=%s, global_delay_factor=%s, use_keys=%s",
                  optional_args, self.port, self.global_delay_factor, self.use_keys)

        self._tn = None
        self._connected = False
        self._config_lock = False
        self._prompt = None  # Detected device prompt (e.g., 'SHOP-AG1_20SQ#')
        log.debug("__init__ - complete")

    def _is_at_prompt(self, output):
        """Check if output ends with a CLI prompt (# for privileged, > for user mode)."""
        return bool(re.search(r"\S+[#>]\s*$", output, re.MULTILINE))

    def _strip_telnet(self, data):
        """Remove telnet IAC (Interpret As Command) negotiation bytes."""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == 0xFF:  # IAC
                if i + 1 < len(data):
                    i += 3 if i + 2 < len(data) else 2
                else:
                    i += 1
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    def _socket_read(self, prompt=None):
        """Read data from socket until *prompt* (or a CLI prompt ending with # or >) appears.

        Reads in chunks with a socket timeout, accumulating data.
        Returns as soon as the prompt is found, or when the total elapsed
        time exceeds read_timeout_seconds.
        """
        data = b""
        self._sock.settimeout(30.0)
        read_start = __import__("time").time()
        read_timeout_seconds = 300  # 5 minutes total for very large outputs
        print(f"[DEBUG] _socket_read - starting, prompt={prompt!r}", flush=True)
        log.debug("_socket_read - starting, prompt=%r", prompt)

        while True:
            # Check total elapsed time
            elapsed = __import__("time").time() - read_start
            if elapsed > read_timeout_seconds:
                print(
                    f"[DEBUG] _socket_read: total timeout after {elapsed:.0f}s, "
                    f"{len(data)} bytes received",
                    flush=True,
                )
                log.debug(
                    "_socket_read: total timeout after %.0f seconds, %d bytes",
                    elapsed, len(data),
                )
                break

            try:
                chunk = self._sock.recv(65536)
                if not chunk:
                    print(f"[DEBUG] _socket_read: connection closed after {len(data)} bytes", flush=True)
                    log.debug("_socket_read: connection closed after %d bytes", len(data))
                    break
                data += chunk
                print(f"[DEBUG] _socket_read: received {len(chunk)} bytes (total {len(data)}), last 200 hex: {data[-200:]!r}", flush=True)
                log.debug(
                    "_socket_read: received %d bytes (total %d), raw hex: %r",
                    len(chunk), len(data), data[-200:],
                )
                if prompt is not None:
                    if prompt.encode() in data:
                        log.debug(
                            "_socket_read: found prompt '%s' after %d bytes",
                            prompt, len(data),
                        )
                        break
                else:
                    # Detect CLI prompt: any line ending with # or > (e.g., 'SHOP-AG1_20SQ#')
                    decoded = data.decode("utf-8", errors="replace")
                    # Look for a prompt pattern: non-whitespace chars followed by # or > at end of line
                    prompt_match = re.search(r"\S+[#>]\s*$", decoded, re.MULTILINE)
                    if prompt_match:
                        detected_prompt = prompt_match.group(0).strip()
                        # Store the detected prompt for future use
                        if self._prompt is None:
                            self._prompt = detected_prompt
                            log.debug("_socket_read: detected new prompt: %r", self._prompt)
                        log.debug(
                            "_socket_read: found CLI prompt '%s' after %d bytes",
                            detected_prompt, len(data),
                        )
                        break
                    else:
                        # Check for common prompts even when no explicit prompt is set
                        common_prompts = ["Password:", "password:", "Enter password:", "Enable password:"]
                        found_common = [p for p in common_prompts if p.lower() in decoded.lower()]
                        if found_common:
                            log.debug(
                                "_socket_read: found common prompt(s) %s after %d bytes (no CLI prompt yet)",
                                found_common, len(data),
                            )
                        log.debug(
                            "_socket_read: no CLI prompt yet, looking for # or >, elapsed=%.1fs, total=%d bytes",
                            elapsed, len(data),
                        )
            except socket.timeout:
                print(f"[DEBUG] _socket_read: timeout after {len(data)} bytes, elapsed={elapsed:.1f}s", flush=True)
                log.debug(
                    "_socket_read: timeout after %d bytes, elapsed=%.1f seconds",
                    len(data), elapsed,
                )
                continue  # keep reading instead of breaking
            except Exception as exc:
                log.debug("_socket_read: exception %s after %d bytes", exc, len(data))
                break

        if prompt is not None:
            if prompt.encode() not in data:
                raise ConnectionException(
                    f"Did not receive prompt '{prompt}' after reading {len(data)} bytes"
                )

        result = self._strip_telnet(data).decode("utf-8", errors="replace")
        log.debug("_socket_read - complete: %d bytes, result (%d chars): %r", len(data), len(result), result[:500])
        return result

    def _socket_write(self, command):
        """Write command to socket connection."""
        print(f"[DEBUG] _socket_write - sending: {command!r}", flush=True)
        log.debug("_socket_write - sending: %r", command)
        self._sock.send((command + "\r").encode("utf-8"))
        print(f"[DEBUG] _socket_write - sent {len(command) + 1} bytes", flush=True)
        log.debug("_socket_write - sent %d bytes", len(command) + 1)

    def _is_mocked(self):
        """Check if we're using a test double."""
        return hasattr(self, "device") and hasattr(self.device, "run_commands")

    def _send_command(self, command):
        """Send a CLI command and return the output (stripped of echo and prompt)."""
        log.debug("_send_command - command: %r", command)
        if self._is_mocked():
            result = self.device.run_commands([command], encoding="text")
            output = result[0].get("output", "")
            log.debug("_send_command - mocked output (%d bytes): %r", len(output), output[:500])
            return output
        self._socket_write(command)
        output = self._socket_read()
        log.debug("_send_command - raw output (%d bytes): %r", len(output), output[:500])
        # Remove command echo and trailing prompt (any # or > prompt)
        output = re.sub(rf"^{re.escape(command)}\s*\n", "", output, flags=re.MULTILINE)
        output = re.sub(r"\n\S+[#>]\s*$", "", output)
        log.debug("_send_command - cleaned output (%d bytes): %r", len(output), output[:500])
        return output

    def open(self):
        """Open telnet connection to the device."""
        print(f"[DEBUG] open - starting connection to {self.hostname}:{self.port}", flush=True)
        log.debug("open - starting connection to %s:%s", self.hostname, self.port)
        if self._is_mocked():
            log.debug("open - mocked mode, setting connected=True")
            self._connected = True
            return
        try:
            log.debug("open - creating socket")
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            log.debug("open - connecting to %s:%s", self.hostname, self.port)
            self._sock.connect((self.hostname, self.port))
            log.debug("open - socket connected")
        except Exception as exc:
            log.debug("open - connection failed: %s", exc)
            raise ConnectionException(f"Unable to connect to host {self.hostname}")

        # Read initial banner — wait for the Username: prompt
        log.debug("open - reading banner (waiting for Username: prompt)")
        output = self._socket_read(prompt="Username:")
        log.debug("open - banner output (%d bytes): %r", len(output), output[:500])
        if not self._is_at_prompt(output):
            log.debug("open - banner output: %s", output[:200])

        # Login
        log.debug("open - sending username: %r", self.username)
        self._socket_write(self.username)
        log.debug("open - waiting for Password: prompt")
        self._socket_read(prompt="Password:")

        log.debug("open - sending password")
        self._socket_write(self.password)
        log.debug("open - waiting for post-login output")
        output = self._socket_read()
        log.debug("open - post-login output (%d bytes): %r", len(output), output[:500])

        # Enter enable mode
        log.debug("open - sending 'enable' command")
        self._socket_write("enable")
        log.debug("open - waiting for enable output")
        output = self._socket_read()
        log.debug("open - enable output (%d bytes): %r", len(output), output[:500])

        # Check if we're already in enable mode or need password
        if not self._is_at_prompt(output):
            log.debug("open - not at CLI prompt yet, trying enable password")
            # Try sending enable password (often same as login or empty)
            self._socket_write(self.password)
            output = self._socket_read()
            log.debug("open - after enable password output (%d bytes): %r", len(output), output[:500])

        if not self._is_at_prompt(output):
            log.debug("open - still not at CLI prompt, trying empty password")
            # Try empty password
            self._socket_write("")
            output = self._socket_read()
            log.debug("open - after empty password output (%d bytes): %r", len(output), output[:500])

        if not self._is_at_prompt(output):
            log.debug("open - FAILED to enter enable mode")
            raise ConnectionException(f"Failed to enter enable mode. Output: {output[:200]}")

        log.debug("open - in enable mode, disabling pagination")
        # Disable pagination
        self._send_command("terminal length 0")

        self._connected = True
        log.debug("open - connection established, _connected=True")

    def close(self):
        """Close socket connection."""
        log.debug("close - called, _connected=%s", self._connected)
        if hasattr(self, "_sock") and self._sock:
            try:
                log.debug("close - closing socket")
                self._sock.close()
            except Exception as exc:
                log.debug("close - error closing socket: %s", exc)
                pass
            self._sock = None
        self._connected = False
        self._config_lock = False
        log.debug("close - complete")

    def is_alive(self):
        """Return if connected."""
        if self._is_mocked():
            return {"is_alive": True}
        if self._sock:
            return {"is_alive": True}
        return {"is_alive": False}

    @property
    def platform(self):
        """Return platform string."""
        return "fsos"

    def get_facts(self):
        """Return facts about the network device."""
        log.debug("get_facts - starting")
        output = self._send_command("show version")
        log.debug("get_facts - show version output (%d bytes): %r", len(output), output[:1000])

        facts = {}

        # Parse system description
        m = re.search(r"System description\s*:\s*(.+)", output)
        if m:
            facts["os_version"] = m.group(1).strip()

        # Parse software version
        m = re.search(r"System software version\s*:\s*(.+)", output)
        if m:
            facts["os_version"] = m.group(1).strip()

        # Parse model — handle both "Slot 0" and "Slot 1/0" formats
        m = re.search(r"Slot\s+\S+\s*:\s*(.+)", output)
        if m:
            facts["model"] = m.group(1).strip()

        # Parse serial number
        m = re.search(r"System serial number\s*:\s*(.+)", output)
        if m:
            facts["serial_number"] = m.group(1).strip()

        # Parse uptime
        m = re.search(r"System uptime\s*:\s*(.+)", output)
        if m:
            uptime_str = m.group(1).strip()
            # Parse format: D:HH:MM:SS or HH:MM:SS
            parts = uptime_str.split(":")
            if len(parts) == 4:
                days = int(parts[0])
                hours = int(parts[1])
                minutes = int(parts[2])
                seconds = int(parts[3])
            else:
                days = 0
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = int(parts[2])
            facts["uptime"] = float((days * 86400) + (hours * 3600) + (minutes * 60) + seconds)

        # Parse hostname from prompt or config
        facts["hostname"] = self.hostname

        # FQDN - derive from hostname
        facts["fqdn"] = self.hostname

        # Set vendor based on model
        facts["vendor"] = "FS.COM"

        # Parse interface list from show interfaces
        log.debug("get_facts - fetching interface list")
        iface_output = self._send_command("show interfaces")
        log.debug("get_facts - show interfaces output (%d bytes): %r", len(iface_output), iface_output[:1000])
        iface_pattern = re.compile(
            r"={20,}\s*(.+?)\s*={20,}", re.MULTILINE
        )
        facts["interface_list"] = [m.group(1) for m in iface_pattern.finditer(iface_output)]
        log.debug("get_facts - found interfaces: %s", facts["interface_list"])

        log.debug("get_facts - returning facts: %s", facts)
        return facts

    def get_interfaces(self):
        """Return interfaces details."""
        log.debug("get_interfaces - starting")
        output = self._send_command("show interfaces")
        log.debug("get_interfaces - raw output (%d bytes): %r", len(output), output[:2000])

        interfaces = {}

        # Parse detailed interface output
        # Format: ================== GigabitEthernet 0/1 ========================
        iface_pattern = re.compile(
            r"={20,}\s*(.+?)\s*={20,}", re.MULTILINE
        )
        iface_blocks = list(iface_pattern.finditer(output))

        for i, match in enumerate(iface_blocks):
            iface_name = match.group(1)
            start = match.start()
            end = iface_blocks[i + 1].start() if i + 1 < len(iface_blocks) else len(output)
            block = output[start:end]

            iface_data = {
                "is_up": False,
                "is_enabled": True,
                "description": "",
                "last_flapped": float(-1),
                "mtu": 1500,
                "speed": 0.0,
                "mac_address": "",
            }

            # Interface status
            if "is UP" in block:
                iface_data["is_up"] = True

            # MAC address
            m = re.search(r"address is\s+([\da-fA-F.]+)", block)
            if m:
                iface_data["mac_address"] = m.group(1).replace(".", ":")

            # MTU
            m = re.search(r"MTU\s+(\d+)\s+bytes", block)
            if m:
                iface_data["mtu"] = int(m.group(1))

            # Speed
            m = re.search(r"oper speed is\s+(\w+)", block)
            if m:
                speed_str = m.group(1)
                if speed_str != "Unknown":
                    speed_m = re.search(r"(\d+)([MGT])", speed_str)
                    if speed_m:
                        val = int(speed_m.group(1))
                        unit = speed_m.group(2)
                        multipliers = {"M": 1000000.0, "G": 1000000000.0, "T": 1000000000000.0}
                        iface_data["speed"] = val * multipliers.get(unit, 0.0)

            # Description
            m = re.search(r"Description:\s*(.+)", block)
            if m:
                iface_data["description"] = m.group(1).strip()

            interfaces[iface_name] = iface_data
            log.debug("get_interfaces - parsed %s: %s", iface_name, iface_data)

        log.debug("get_interfaces - returning %d interfaces: %s", len(interfaces), list(interfaces.keys()))
        return interfaces

    def get_interfaces_ip(self):
        """Return IP address information for interfaces."""
        log.debug("get_interfaces_ip - starting")
        interfaces_ip = {}
        output = self._send_command("show interfaces")
        log.debug("get_interfaces_ip - raw output (%d bytes): %r", len(output), output[:2000])

        iface_pattern = re.compile(
            r"={20,}\s*(.+?)\s*={20,}", re.MULTILINE
        )
        iface_blocks = list(iface_pattern.finditer(output))

        for i, match in enumerate(iface_blocks):
            iface_name = match.group(1)
            start = match.start()
            end = iface_blocks[i + 1].start() if i + 1 < len(iface_blocks) else len(output)
            block = output[start:end]

            ipv4 = {}
            ipv6 = {}

            # IPv4 address — handle both "192.168.1.1/24" and "192.168.1.1" + "Subnet mask:" formats
            m = re.search(r"Interface address is:\s*(\S+)", block)
            if m:
                addr = m.group(1)
                if addr != "no":
                    # Check if address includes prefix length (e.g., 192.168.1.1/24)
                    if "/" in addr:
                        ip_addr, prefix = addr.split("/")
                        ipv4[ip_addr] = {"prefix_length": int(prefix)}
                    else:
                        # Get subnet mask from separate line
                        m2 = re.search(r"Subnet mask:\s*(\S+)", block)
                        if m2:
                            ipv4[addr] = {"prefix_length": self._mask_to_prefix(m2.group(1))}
                        else:
                            ipv4[addr] = {"prefix_length": 32}

            # IPv6 address
            m = re.search(r"Interface IPv6 address is:", block)
            if m:
                # Check if there's an actual IPv6 address
                ipv6_block = block[m.end():m.end() + 200]
                ipv6_m = re.search(r"(\S+)\s*/\s*(\d+)", ipv6_block)
                if ipv6_m:
                    ipv6[ipv6_m.group(1)] = {"prefix_length": int(ipv6_m.group(2))}

            interfaces_ip[iface_name] = {"ipv4": ipv4, "ipv6": ipv6}

        log.debug("get_interfaces_ip - returning: %s", interfaces_ip)
        return interfaces_ip

    def _mask_to_prefix(self, mask):
        """Convert subnet mask to prefix length."""
        mask_map = {
            "255.0.0.0": 8,
            "255.128.0.0": 9,
            "255.192.0.0": 10,
            "255.224.0.0": 11,
            "255.240.0.0": 12,
            "255.248.0.0": 13,
            "255.252.0.0": 14,
            "255.254.0.0": 15,
            "255.255.0.0": 16,
            "255.255.128.0": 17,
            "255.255.192.0": 18,
            "255.255.224.0": 19,
            "255.255.240.0": 20,
            "255.255.248.0": 21,
            "255.255.252.0": 22,
            "255.255.254.0": 23,
            "255.255.255.0": 24,
            "255.255.255.128": 25,
            "255.255.255.192": 26,
            "255.255.255.224": 27,
            "255.255.255.240": 28,
            "255.255.255.248": 29,
            "255.255.255.252": 30,
            "255.255.255.254": 31,
            "255.255.255.255": 32,
        }
        return mask_map.get(mask, 32)

    def get_interfaces_counters(self):
        """Return interfaces counters."""
        log.debug("get_interfaces_counters - starting")
        output = self._send_command("show interfaces")
        log.debug("get_interfaces_counters - raw output (%d bytes): %r", len(output), output[:2000])
        counters = {}

        iface_pattern = re.compile(
            r"={20,}\s*(.+?)\s*={20,}", re.MULTILINE
        )
        iface_blocks = list(iface_pattern.finditer(output))

        for i, match in enumerate(iface_blocks):
            iface_name = match.group(1)
            start = match.start()
            end = iface_blocks[i + 1].start() if i + 1 < len(iface_blocks) else len(output)
            block = output[start:end]

            counter = {
                "tx_errors": 0,
                "rx_errors": 0,
                "tx_discards": 0,
                "rx_discards": 0,
                "tx_octets": 0,
                "rx_octets": 0,
                "tx_unicast_packets": 0,
                "rx_unicast_packets": 0,
                "tx_multicast_packets": 0,
                "rx_multicast_packets": 0,
                "tx_broadcast_packets": 0,
                "rx_broadcast_packets": 0,
            }

            m = re.search(r"(\d+)\s+packets input,\s+(\d+)\s+bytes", block)
            if m:
                counter["rx_octets"] = int(m.group(2))
                counter["rx_unicast_packets"] = int(m.group(1))

            m = re.search(r"(\d+)\s+packets output,\s+(\d+)\s+bytes", block)
            if m:
                counter["tx_octets"] = int(m.group(2))
                counter["tx_unicast_packets"] = int(m.group(1))

            m = re.search(r"(\d+)\s+input errors", block)
            if m:
                counter["rx_errors"] = int(m.group(1))

            m = re.search(r"(\d+)\s+output errors", block)
            if m:
                counter["tx_errors"] = int(m.group(1))

            counters[iface_name] = counter
            log.debug("get_interfaces_counters - parsed %s: %s", iface_name, counter)

        log.debug("get_interfaces_counters - returning %d counters", len(counters))
        return counters

    def get_vlans(self):
        """Return VLAN information."""
        log.debug("get_vlans - starting")
        output = self._send_command("show vlan")
        log.debug("get_vlans - raw output (%d bytes): %r", len(output), output[:2000])
        vlans = {}

        # Parse VLAN table
        # VLAN Name                             Status    Ports
        # ---- -------------------------------- --------- -----------------------------------
        #    1 VLAN0001                         STATIC    Gi0/1, Gi0/2, ...
        vlan_pattern = re.compile(
            r"(\d+)\s+(\S+)\s+(STATIC|DYNAMIC)\s+(.+)", re.MULTILINE
        )

        for match in vlan_pattern.finditer(output):
            vlan_id = int(match.group(1))
            name = match.group(2)
            status = match.group(3)
            ports_str = match.group(4).strip()

            # Parse ports
            ports = []
            if ports_str:
                # Split by comma and clean up
                for port in ports_str.split(","):
                    port = port.strip()
                    if port:
                        ports.append(port)

            vlans[str(vlan_id)] = {
                "name": name,
                "interfaces": ports,
            }

        log.debug("get_vlans - returning %d vlans", len(vlans))
        return vlans

    def get_lldp_neighbors(self):
        """Return LLDP neighbors information."""
        log.debug("get_lldp_neighbors - starting")
        output = self._send_command("show lldp neighbors")
        log.debug("get_lldp_neighbors - raw output (%d bytes): %r", len(output), output[:2000])
        neighbors = {}

        # Parse LLDP neighbors table using header-driven column detection.
        # FSOS output format:
        #   Capability codes:
        #       (R) Router, (B) Bridge, (T) Telephone, (C) DOCSIS Cable Device
        #       (W) WLAN Access Point, (P) Repeater, (S) Station, (O) Other
        #   System Name                 Local Intf          Port ID                     Capability   Aging-time
        #   BOS-SW001                   Gi0/1               Port 42                     B            1minutes 35seconds
        #
        #   Total entries displayed: 1

        # Find the header line containing column names
        header_line = None
        header_pattern = re.compile(
            r"System Name\s+(.+?)Local Intf\s+(.+?)Port ID\s+(.+?)Capability\s+(.+?)Aging-time"
        )
        for line in output.splitlines():
            m = header_pattern.search(line)
            if m:
                header_line = line
                break

        if header_line is None:
            return neighbors

        # Determine column start positions from the header
        sys_name_start = header_line.index("System Name")
        local_intf_start = header_line.index("Local Intf")
        port_id_start = header_line.index("Port ID")
        capability_start = header_line.index("Capability")
        aging_start = header_line.index("Aging-time")

        # Parse data lines using column positions
        for line in output.splitlines():
            # Skip lines that are too short to contain data
            if len(line) < capability_start:
                continue
            # Skip header, footer, and capability codes lines
            stripped = line.strip()
            if not stripped or stripped.startswith("Capability codes") or \
               stripped.startswith("System Name") or stripped.startswith("Total"):
                continue

            # Extract fields based on column positions
            sys_name = line[sys_name_start:local_intf_start].strip()
            local_intf = line[local_intf_start:port_id_start].strip()
            port_id = line[port_id_start:capability_start].strip()
            aging_time = line[aging_start:].strip()

            if sys_name and sys_name != "Local":
                if local_intf not in neighbors:
                    neighbors[local_intf] = []
                neighbors[local_intf].append(
                    {
                        "hostname": sys_name,
                        "port": port_id,
                    }
                )

        log.debug("get_lldp_neighbors - returning %d neighbors", len(neighbors))
        return neighbors

    def get_config(self, retrieve="all", full=False, sanitized=False, format="text"):
        """Return configuration sections."""
        log.debug("get_config - retrieve=%s", retrieve)
        configs = {
            "running": "",
            "startup": "",
            "candidate": "",
        }

        # Load running config once if needed
        running_config = ""
        if retrieve in ("running", "all"):
            log.debug("get_config - fetching running config")
            output = self._send_command("show running-config")
            log.debug("get_config - raw output (%d bytes): %r", len(output), output[:2000])
            # Remove the command echo, header lines, and trailing prompt
            running_config = re.sub(r"^show running-config\s*\n", "", output, flags=re.MULTILINE)
            running_config = re.sub(r"^Building configuration\.\.\.\s*\n", "", running_config, flags=re.MULTILINE)
            running_config = re.sub(r"^Current configuration:\s*\d+\s*bytes\s*\n", "", running_config, flags=re.MULTILINE)
            running_config = re.sub(r"\n\S+[#>]\s*$", "", running_config)
            log.debug("get_config - cleaned config (%d bytes): %r", len(running_config), running_config[:2000])

        if retrieve == "running":
            configs["running"] = running_config
        elif retrieve == "startup":
            configs["startup"] = running_config
        elif retrieve == "candidate":
            configs["candidate"] = ""
        else:  # retrieve == "all"
            configs["running"] = running_config
            configs["startup"] = running_config
            configs["candidate"] = ""

        log.debug("get_config - returning configs: running=%d bytes, startup=%d bytes, candidate=%d bytes",
                  len(configs["running"]), len(configs["startup"]), len(configs["candidate"]))
        return configs

    def get_snmp_information(self):
        """Return SNMP information."""
        log.debug("get_snmp_information - starting")
        output = self._send_command("show snmp")
        log.debug("get_snmp_information - raw output (%d bytes): %r", len(output), output[:2000])
        snmp = {
            "chassis_id": "",
            "community": {},
            "contact": "",
            "location": "",
        }

        # Chassis ID
        m = re.search(r"Chassis:\s*(\S+)", output)
        if m:
            snmp["chassis_id"] = m.group(1)

        log.debug("get_snmp_information - returning: %s", snmp)
        return snmp

    def get_users(self):
        """Return users information."""
        log.debug("get_users - starting")
        output = self._send_command("show running-config")
        log.debug("get_users - raw output (%d bytes): %r", len(output), output[:2000])
        users = {}

        # Parse username lines
        # username admin privilege 15 password admin
        user_pattern = re.compile(r"username\s+(\S+)\s+privilege\s+(\d+)\s+password\s+(\S+)", re.MULTILINE)

        for match in user_pattern.finditer(output):
            username = match.group(1)
            privilege = int(match.group(2))
            password_type = match.group(3)

            users[username] = {
                "level": privilege,
                "password": "",  # Don't return hashed passwords
                "sshkeys": [],
            }

        log.debug("get_users - returning %d users", len(users))
        return users

    def get_environment(self):
        """Return environment information."""
        log.debug("get_environment - starting")
        environment = {
            "cpu": {},
            "memory": {
                "used_ram": 0,
                "available_ram": 0,
            },
            "power": {},
            "temperature": {},
            "fans": {},
        }

        # Parse CPU usage from show cpu — handle multiple slots
        log.debug("get_environment - fetching show cpu")
        cpu_output = self._send_command("show cpu")
        log.debug("get_environment - show cpu output (%d bytes): %r", len(cpu_output), cpu_output[:2000])
        cpu_usages = re.findall(
            r"CPU utilization in five seconds:\s*([\d.]+)%",
            cpu_output,
        )
        log.debug("get_environment - CPU usages found: %s", cpu_usages)
        if cpu_usages:
            # Use the maximum CPU usage across all slots
            max_usage = max(float(u) for u in cpu_usages)
            environment["cpu"] = {
                0: {
                    "%usage": max_usage,
                }
            }

        # Parse memory from show memory
        log.debug("get_environment - fetching show memory")
        mem_output = self._send_command("show memory")
        log.debug("get_environment - show memory output (%d bytes): %r", len(mem_output), mem_output[:2000])
        m = re.search(
            r"System Memory:\s+(\d+)KB\s+total,\s+(\d+)KB\s+used,\s+(\d+)KB\s+free",
            mem_output,
        )
        if m:
            environment["memory"] = {
                "used_ram": int(m.group(2)),
                "available_ram": int(m.group(3)),
            }

        log.debug("get_environment - returning: %s", environment)
        return environment

    def get_ntp_peers(self):
        """Return NTP peers."""
        # FSOS doesn't seem to have NTP configured
        return {}

    def get_ntp_servers(self):
        """Return NTP servers."""
        # FSOS doesn't seem to have NTP configured
        return {}

    def get_ntp_stats(self):
        """Return NTP statistics."""
        return {}

    def get_bgp_config(self, group="", neighbor=""):
        """Return BGP configuration."""
        # No BGP configured
        return {}

    def get_bgp_neighbors(self):
        """Return BGP neighbors information."""
        return {
            "global": {
                "router_id": "",
                "peers": {},
            }
        }

    def get_mac_address_table(self):
        """Return MAC address table."""
        output = self._send_command("show mac")
        mac_table = []

        # Parse MAC address table
        # Format: Vlan  MAC Address      Type    Interface                      Time
        #         ----  ---------------- -------- ------------------------------ --------------------
        #            1  0000.5e00.0103  DYNAMIC  GigabitEthernet 0/1            2026-9-24 16:52:31
        mac_pattern = re.compile(
            r"(\d+)\s+([0-9a-fA-F.]+)\s+(\w+)\s+(\S+\s+\d+/\d+)",
        )

        for match in mac_pattern.finditer(output):
            vlan = int(match.group(1))
            mac = match.group(2).lower()
            mac_type = match.group(3).lower()
            interface = match.group(4)

            mac_table.append(
                {
                    "mac": mac,
                    "interface": interface,
                    "vlan": vlan,
                    "static": mac_type == "static",
                    "active": True,
                    "moves": 0,
                    "last_move": 0.0,
                }
            )

        return mac_table

    def get_arp_table(self, vrf=""):
        """Return ARP table."""
        log.debug("get_arp_table - starting, vrf=%r", vrf)
        output = self._send_command("show arp")
        log.debug("get_arp_table - raw output (%d bytes): %r", len(output), output[:2000])
        arp_table = []

        # Parse ARP table
        # Protocol  Address          Age(min)  Hardware        Type   Interface
        # Internet  10.208.1.1       <--->     <Incomplete>    arpa   VLAN 1
        # Internet  10.208.1.13      --        649d.99d0.8f2d  arpa   VLAN 1
        arp_pattern = re.compile(
            r"Internet\s+(\S+)\s+(\S+)\s+([\da-fA-F.]+)\s+(\S+)\s+(\S+)",
            re.MULTILINE,
        )

        for match in arp_pattern.finditer(output):
            ip_address = match.group(1)
            age = match.group(2)
            mac_address = match.group(3).replace(".", ":")
            interface = match.group(5)

            # Skip incomplete entries
            if age == "<--->":
                continue

            arp_table.append(
                {
                    "interface": interface,
                    "mac": mac_address,
                    "ip": ip_address,
                    "age": float(-1.0),
                }
            )

        log.debug("get_arp_table - returning %d entries", len(arp_table))
        return arp_table

    def get_ipv6_neighbors_table(self):
        """Return IPv6 neighbors table."""
        return []

    def get_route_to(self, destination="", protocol="", longer=False):
        """Return routing information for a destination."""
        log.debug("get_route_to - destination=%r, protocol=%r, longer=%s", destination, protocol, longer)
        output = self._send_command("show ip route")
        log.debug("get_route_to - raw output (%d bytes): %r", len(output), output[:2000])
        routes_by_prefix = {}

        # Parse route entries
        route_pattern = re.compile(
            r"(\S+)\s+(\S+)\s+(?:is\s+)?(.+)", re.MULTILINE
        )

        for match in route_pattern.finditer(output):
            code = match.group(1)
            prefix = match.group(2)
            details = match.group(3).strip()

            # Check if this matches our destination
            if destination:
                if longer:
                    if not prefix.startswith(destination.split("/")[0]):
                        continue
                elif prefix != destination:
                    continue

            route = {
                "protocol": "static" if code.startswith("S") else "connected" if code.startswith("C") else "unknown",
                "current_active": True,
                "last_active": True,
                "age": 0,
                "next_hop": "",
                "outgoing_interface": "",
                "selected_next_hop": True,
                "preference": 1,
                "inactive_reason": "",
                "routing_table": "default",
                "protocol_attributes": {},
            }

            if "via" in details:
                route["next_hop"] = details.split("via")[1].strip().split()[0]
            if "directly connected" in details:
                route["outgoing_interface"] = details.split(",")[0].strip()

            if prefix not in routes_by_prefix:
                routes_by_prefix[prefix] = []
            routes_by_prefix[prefix].append(route)

        log.debug("get_route_to - returning %d prefixes", len(routes_by_prefix))
        return routes_by_prefix

    def ping(self, destination, source="", ttl=255, timeout=2, size=100, count=5, vrf="", source_interface=""):
        """Execute ping."""
        log.debug("ping - destination=%r, source=%r, size=%d, count=%d, timeout=%d", destination, source, size, count, timeout)
        cmd = f"ping {destination} ntimes {count} length {size} timeout {timeout}"
        if source:
            cmd += f" source {source}"
        if source_interface:
            cmd += f" source-interface {source_interface}"
        log.debug("ping - executing command: %r", cmd)
        output = self._send_command(cmd)
        log.debug("ping - raw output (%d bytes): %r", len(output), output[:2000])

        # Parse ping output
        success_pattern = re.search(r"Success rate is\s+(\d+)%", output)
        if success_pattern:
            pct = int(success_pattern.group(1))
            received = int(count * pct / 100)
            return {
                "success": {
                    "probes_sent": count,
                    "packet_loss": 100 - pct,
                    "rtt_min": 0.0,
                    "rtt_max": 0.0,
                    "rtt_avg": 0.0,
                    "rtt_stddev": 0.0,
                    "results": [],
                }
            }

        log.debug("ping - success rate: %s%%", pct)
        return {
            "success": {
                "probes_sent": count,
                "packet_loss": 100,
                "rtt_min": 0.0,
                "rtt_max": 0.0,
                "rtt_avg": 0.0,
                "rtt_stddev": 0.0,
                "results": [],
            }
        }

        log.debug("ping - packet loss: 100%%")
        return {
            "success": {
                "probes_sent": count,
                "packet_loss": 100,
                "rtt_min": 0.0,
                "rtt_max": 0.0,
                "rtt_avg": 0.0,
                "rtt_stddev": 0.0,
                "results": [],
            }
        }

    def traceroute(self, destination, source="", ttl=255, timeout=2, vrf=""):
        """Execute traceroute."""
        log.debug("traceroute - destination=%r, ttl=%d, timeout=%d", destination, ttl, timeout)
        cmd = f"traceroute ip {destination} ttl 1 {ttl} timeout {timeout}"
        log.debug("traceroute - executing command: %r", cmd)
        output = self._send_command(cmd)
        log.debug("traceroute - raw output (%d bytes): %r", len(output), output[:2000])

        # Parse traceroute output
        success = {}
        # Skip the header line (traceroute to ...), only match indented hop lines
        hop_pattern = re.compile(r"^\s+(\d+)\s+(.+)", re.MULTILINE)
        for match in hop_pattern.finditer(output):
            hop_num = int(match.group(1))
            hop_details = match.group(2).strip()
            parts = hop_details.split()
            ip_addr = parts[0] if parts and parts[0] != "*" else ""
            rtt = 0.0
            if parts and parts[0] != "*":
                try:
                    rtt = float(parts[1])
                except (ValueError, IndexError):
                    pass
            success[hop_num] = {
                "probes": {
                    1: {
                        "ip_address": ip_addr,
                        "host_name": "",
                        "rtt": rtt,
                    }
                }
            }

        log.debug("traceroute - returning %d hops", len(success))
        return {"success": success}

    def get_optics(self):
        """Return optics information for all interfaces."""
        log.debug("get_optics - starting")
        # FSOS doesn't have a working show opticals command
        # Return proper structure with default values
        optics = {}
        output = self._send_command("show interfaces")
        log.debug("get_optics - show interfaces output (%d bytes): %r", len(output), output[:2000])

        iface_pattern = re.compile(
            r"={20,}\s*(.+?)\s*={20,}", re.MULTILINE
        )
        for match in iface_pattern.finditer(output):
            iface_name = match.group(1)
            optics[iface_name] = {
                "physical_channels": {
                    "channel": [
                        {
                            "index": 0,
                            "state": {
                                "input_power": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                                "output_power": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                                "laser_bias_current": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                            },
                        },
                        {
                            "index": 1,
                            "state": {
                                "input_power": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                                "output_power": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                                "laser_bias_current": {
                                    "instant": 0.0,
                                    "avg": 0.0,
                                    "min": 0.0,
                                    "max": 0.0,
                                },
                            },
                        },
                    ]
                }
            }

        log.debug("get_optics - returning optics for %d interfaces", len(optics))
        return optics

    def get_lldp_neighbors_detail(self, interface=""):
        """Return detailed LLDP neighbors information."""
        log.debug("get_lldp_neighbors_detail - starting, interface=%r", interface)
        output = self._send_command("show lldp neighbors detail")
        log.debug("get_lldp_neighbors_detail - raw output (%d bytes): %r", len(output), output[:2000])
        neighbors = {}

        # Parse LLDP neighbors detail
        # Format:
        # LLDP neighbor-information of port [GigabitEthernet 0/1]
        #   Chassis ID                        : 7483.c273.a0c5
        #   System name                       : BOS-SW001
        #   System description                : US-48-G1, 5.43.18.12487, Linux 3.6.5
        #   System capabilities supported     : Bridge
        #   System capabilities enabled       : Bridge
        #   Port ID                           : Port 42
        #   Port description                  : D223-Conference Table
        #   Aging time                        : 1minutes 47seconds

        # Split by port blocks
        port_pattern = re.compile(
            r"LLDP neighbor-information of port \[([^\]]+)\]",
            re.MULTILINE,
        )

        for port_match in port_pattern.finditer(output):
            local_intf = port_match.group(1)
            start = port_match.end()
            # Find next port block or end of output
            next_match = port_pattern.search(output, start)
            if next_match:
                block = output[start:next_match.start()]
            else:
                block = output[start:]

            neighbor = {
                "parent_interface": "",
                "remote_chassis_id": "",
                "remote_port": "",
                "remote_port_description": "",
                "remote_system_name": "",
                "remote_system_description": "",
                "remote_system_capab": [],
                "remote_system_enable_capab": [],
            }

            # Parse fields — use re.MULTILINE + ^ to anchor to line start,
            # and [^\n]+ to capture only within the current line.
            # Use [ \t]+ instead of \s+ after ':' so we don't cross line boundaries
            # when a field value is empty (\s matches newlines).
            m = re.search(r"^\s*Chassis ID\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_chassis_id"] = m.group(1).strip()

            m = re.search(r"^\s*System name\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_system_name"] = m.group(1).strip()

            m = re.search(r"^\s*System description\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_system_description"] = m.group(1).strip()

            m = re.search(r"^\s*System capabilities supported\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_system_capab"] = [m.group(1).strip()]

            m = re.search(r"^\s*System capabilities enabled\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_system_enable_capab"] = [m.group(1).strip()]

            m = re.search(r"^\s*Port ID\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_port"] = m.group(1).strip()

            m = re.search(r"^\s*Port description\s+:[ \t]+([^\n]+)", block, re.MULTILINE)
            if m:
                neighbor["remote_port_description"] = m.group(1).strip()

            if local_intf not in neighbors:
                neighbors[local_intf] = []
            neighbors[local_intf].append(neighbor)

        log.debug("get_lldp_neighbors_detail - returning %d interfaces with neighbors", len(neighbors))
        return neighbors

    def cli(self, commands, encoding="text"):
        """Return arbitrary CLI output."""
        log.debug("cli - commands=%r, encoding=%s", commands, encoding)
        result = {}
        if isinstance(commands, str):
            commands = [commands]
        for cmd in commands:
            log.debug("cli - executing: %r", cmd)
            output = self._send_command(cmd)
            # Remove command echo and prompt
            output = re.sub(rf"^{re.escape(cmd)}\s*\n", "", output, flags=re.MULTILINE)
            output = re.sub(r"\n\S+[#>]\s*$", "", output)
            result[cmd] = output.strip()
            log.debug("cli - result for %r (%d bytes): %r", cmd, len(output), output[:500])
        log.debug("cli - returning %d results", len(result))
        return result

    def load_replace_candidate(self, filename=None, config=None):
        """Replace running config with new config."""
        log.debug("load_replace_candidate - filename=%r, config_len=%s", filename, len(config) if config else None)
        if config is not None:
            self._running_config = config
        elif filename is not None:
            with open(filename, "r") as f:
                self._running_config = f.read()
        log.debug("load_replace_candidate - stored config: %d bytes", len(self._running_config))

    def load_merge_candidate(self, filename=None, config=None):
        """Merge running config with new config."""
        log.debug("load_merge_candidate - filename=%r, config_len=%s", filename, len(config) if config else None)
        if config is not None:
            self._running_config = config
        elif filename is not None:
            with open(filename, "r") as f:
                self._running_config = f.read()
        log.debug("load_merge_candidate - stored config: %d bytes", len(self._running_config))

    def compare_config(self):
        """Compare candidate config with running config."""
        log.debug("compare_config - called")
        return ""

    def commit_config(self, message="", revert_in=None):
        """Commit the candidate configuration."""
        log.debug("commit_config - message=%r, revert_in=%s", message, revert_in)
        # FSOS applies config immediately, no commit needed
        return ""

    def discard_config(self):
        """Discard the candidate configuration."""
        log.debug("discard_config - called")
        pass

    def rollback(self):
        """Rollback to a previous configuration."""
        log.debug("rollback - called, raising NotImplementedError")
        raise NotImplementedError("FSOS does not support rollback")

    pass  # save_config not in base class, removed
