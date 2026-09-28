#!/usr/bin/env python3
"""Generate expected_result.json files from captured CLI output.

For each switch type in captured/ and each test_get_* method, runs the
driver (via FakeFsosDevice) and saves the result to
captured/expected/{switch_type}/{test_name}.json
"""
import json
import os
import re
import sys

# Ensure project root is on sys.path
# scripts/generate_expected.py -> project root is two levels up
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from napalm_fsos import fsos

SWITCH_TYPES = ['S3410-48TS-P', 'S5860-20SQ']
CAPTURED_DIR = os.path.join(ROOT, 'captured')
EXPECTED_DIR = os.path.join(CAPTURED_DIR, 'expected')

# Map of test method name -> list of commands it runs
# We derive this from the driver source
TEST_COMMANDS = {
    'test_get_facts': ['show version', 'show interfaces'],
    'test_get_interfaces': ['show interfaces'],
    'test_get_interfaces_ip': ['show interfaces'],
    'test_get_interfaces_counters': ['show interfaces'],
    'test_get_vlans': ['show vlan'],
    'test_get_lldp_neighbors': ['show lldp neighbors'],
    'test_get_lldp_neighbors_detail': ['show lldp neighbors detail'],
    'test_get_config': ['show running-config'],
    'test_get_config_filtered': ['show running-config'],
    'test_get_config_sanitized': ['show running-config'],
    'test_get_config_sanitized_filtered': ['show running-config'],
    'test_get_environment': ['show cpu', 'show memory'],  # needs show_memory.txt
    'test_get_bgp_neighbors': [],  # returns hardcoded default
    'test_get_bgp_config': [],  # returns hardcoded default
    'test_get_arp_table': ['show arp'],
    'test_get_arp_table_with_vrf': ['show arp'],  # same commands, VRF param differs
    'test_get_ipv6_neighbors_table': [],  # returns hardcoded []
    'test_get_snmp_information': ['show snmp'],
    'test_get_users': ['show running-config'],
    'test_get_mac_address_table': ['show mac'],
    'test_get_route_to': ['show ip route'],
    'test_get_route_to_longer': ['show ip route'],
    'test_get_optics': ['show interfaces'],
    'test_get_probes_config': [],  # not implemented
    'test_get_probes_results': [],  # not implemented
    'test_ping': ['ping 1.1.1.1 ntimes 5 length 100 timeout 2'],
    'test_ping_failure': ['ping 2.2.2.2 ntimes 5 length 100 timeout 2'],
    'test_traceroute': ['traceroute ip 1.1.1.1 ttl 1 30 timeout 2'],
    'test_traceroute_failure': ['traceroute ip 2.2.2.2 ttl 1 30 timeout 2'],
    'test_get_ntp_peers': [],  # returns {}
    'test_get_ntp_servers': [],  # returns {}
    'test_get_ntp_stats': [],  # returns {}
    'test_get_network_instances': [],  # not implemented
    'test_get_firewall_policies': [],  # not implemented
    'test_is_alive': [],  # just checks _connected flag
    # Tests that require commands not captured on these switches:
    # - test_get_environment: needs show_memory.txt

}


class FakeFsosDevice:
    """Minimal test double that reads from captured/."""

    def __init__(self, switch_type):
        self.switch_type = switch_type
        self.current_test = ''
        self.current_test_case = switch_type
        self._connected = True
        self.patched_attrs = ['device']

    def find_file(self, filename):
        path = os.path.join(CAPTURED_DIR, self.switch_type, filename)
        if not os.path.exists(path):
            raise IOError(f"Couldn't find: {path}")
        return path

    @staticmethod
    def read_txt_file(path):
        with open(path) as f:
            return f.read()

    @staticmethod
    def read_json_file(path):
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def sanitize_text(text):
        return re.sub(r'[^a-zA-Z0-9]', '_', text)[:150]

    def run_commands(self, command_list, encoding='json'):
        result = []
        for cmd in command_list:
            ext = 'json' if encoding == 'json' else 'txt'
            fname = f'{self.sanitize_text(cmd)}.{ext}'
            fpath = self.find_file(fname)
            if encoding == 'json':
                result.append(self.read_json_file(fpath))
            else:
                result.append({'output': self.read_txt_file(fpath)})
        return result

    @property
    def expected_result(self):
        """Not used — we capture actual results directly."""
        return None


class PatchedFsosDriver(fsos.FsosDriver):
    """Driver patched to use FakeFsosDevice."""

    def __init__(self, captured_switch_type, *args, **kwargs):
        # Use "127.0.0.1" as hostname (matches NAPALM_HOSTNAME in tests)
        super().__init__('127.0.0.1', *args, **kwargs)
        self.device = FakeFsosDevice(captured_switch_type)


def run_test(test_name, switch_type):
    """Run a single test and return the result dict."""
    driver = PatchedFsosDriver(switch_type, 'admin', 'admin')
    driver._connected = True
    device = driver.device
    device.current_test = test_name
    # current_test_case is already set to switch_type in FakeFsosDevice.__init__

    # Map test names to driver methods
    method_map = {
        'test_is_alive': lambda: driver.is_alive(),
        'test_get_facts': lambda: driver.get_facts(),
        'test_get_interfaces': lambda: driver.get_interfaces(),
        'test_get_interfaces_ip': lambda: driver.get_interfaces_ip(),
        'test_get_interfaces_counters': lambda: driver.get_interfaces_counters(),
        'test_get_vlans': lambda: driver.get_vlans(),
        'test_get_lldp_neighbors': lambda: driver.get_lldp_neighbors(),
        'test_get_lldp_neighbors_detail': lambda: driver.get_lldp_neighbors_detail(),
        'test_get_config': lambda: driver.get_config(),
        'test_get_config_filtered': lambda: {
            'running': '',
            'startup': '',
            'candidate': '',
        },
        'test_get_config_sanitized': lambda: driver.get_config(sanitized=True),
        'test_get_config_sanitized_filtered': lambda: {
            'running': driver.get_config(retrieve='running', sanitized=True)['running'],
            'startup': driver.get_config(retrieve='startup', sanitized=True)['running'],
            'candidate': '',
        },
        'test_get_environment': lambda: driver.get_environment(),
        'test_get_bgp_neighbors': lambda: driver.get_bgp_neighbors(),
        'test_get_bgp_config': lambda: driver.get_bgp_config(),
        'test_get_arp_table': lambda: driver.get_arp_table(),
        'test_get_arp_table_with_vrf': lambda: driver.get_arp_table(vrf='TEST'),
        'test_get_ipv6_neighbors_table': lambda: driver.get_ipv6_neighbors_table(),
        'test_get_snmp_information': lambda: driver.get_snmp_information(),
        'test_get_users': lambda: driver.get_users(),
        'test_get_mac_address_table': lambda: driver.get_mac_address_table(),
        'test_get_route_to': lambda: driver.get_route_to(destination='1.0.4.0/24', protocol='bgp'),
        'test_get_route_to_longer': lambda: driver.get_route_to(
            destination='1.0.4.0/24', protocol='bgp', longer=True,
        ),
        'test_get_optics': lambda: driver.get_optics(),
        'test_ping': lambda: driver.ping('1.1.1.1'),
        'test_ping_failure': lambda: driver.ping('2.2.2.2'),
        'test_traceroute': lambda: driver.traceroute('1.1.1.1', ttl=30),
        'test_traceroute_failure': lambda: driver.traceroute('2.2.2.2', ttl=30),
        'test_get_ntp_peers': lambda: driver.get_ntp_peers(),
        'test_get_ntp_servers': lambda: driver.get_ntp_servers(),
        'test_get_ntp_stats': lambda: driver.get_ntp_stats(),
    }

    # Tests that can't run without additional captured data
    skipped = {
        'test_get_environment': 'needs show_memory.txt (not captured)',

    }
    if test_name in skipped:
        print(f"  SKIP {test_name}: {skipped[test_name]}")
        return None

    method = method_map.get(test_name)
    if method is None:
        print(f"  SKIP {test_name} (no method map entry)")
        return None

    try:
        result = method()
        return result
    except Exception as e:
        print(f"  ERROR {test_name}: {e}")
        return None


def main():
    os.makedirs(EXPECTED_DIR, exist_ok=True)

    # Determine which tests to generate
    if len(sys.argv) > 1:
        # Specific test names or switch types given as args
        args = sys.argv[1:]
        tests_to_run = [a for a in args if a.startswith('test_')]
        switches = [a for a in args if not a.startswith('test_')]
        if not tests_to_run:
            tests_to_run = sorted(TEST_COMMANDS.keys())
        if not switches:
            switches = SWITCH_TYPES
    else:
        tests_to_run = sorted(TEST_COMMANDS.keys())
        switches = SWITCH_TYPES

    total = len(tests_to_run) * len(switches)
    done = 0

    for switch_type in switches:
        switch_dir = os.path.join(EXPECTED_DIR, switch_type)
        os.makedirs(switch_dir, exist_ok=True)

        for test_name in tests_to_run:
            done += 1
            print(f"[{done}/{total}] {switch_type}/{test_name}.json ... ", end='')

            result = run_test(test_name, switch_type)
            if result is None:
                print("SKIP")
                continue

            out_file = os.path.join(switch_dir, f'{test_name}.json')
            with open(out_file, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            print(f"written ({os.path.getsize(out_file)} bytes)")


if __name__ == '__main__':
    main()
