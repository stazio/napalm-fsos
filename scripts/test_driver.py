#!/usr/bin/env python3
"""Quick test of FsosDriver against real switch."""

import sys
import json

sys.path.insert(0, "/tmp/napalm-lib")
sys.path.insert(0, "/home/staz/Programming/napalm-fsos")

from napalm_fsos.fsos import FsosDriver

HOST = "10.208.1.13"
PORT = 23
USERNAME = "admin"
PASSWORD = "admin"

def test_driver():
    driver = FsosDriver(HOST, USERNAME, PASSWORD, timeout=10, optional_args={"port": PORT})
    
    try:
        print("Opening connection...")
        driver.open()
        print("Connected!")

        print("\n=== is_alive ===")
        print(json.dumps(driver.is_alive(), indent=2))

        print("\n=== get_facts ===")
        facts = driver.get_facts()
        print(json.dumps(facts, indent=2))

        print("\n=== get_interfaces (summary) ===")
        interfaces = driver.get_interfaces()
        for name, info in list(interfaces.items())[:3]:
            print(f"  {name}: up={info['is_up']}, mac={info['mac_address']}, speed={info['speed']}")
        print(f"  ... total {len(interfaces)} interfaces")

        print("\n=== get_vlans ===")
        vlans = driver.get_vlans()
        for vid, vinfo in vlans.items():
            print(f"  VLAN {vid}: {vinfo['name']} ({len(vinfo['interfaces'])} ports)")

        print("\n=== get_config (running) ===")
        config = driver.get_config()
        print(f"  Running config length: {len(config.get('running', ''))} bytes")

        print("\n=== get_arp_table ===")
        arp = driver.get_arp_table()
        for entry in arp:
            print(f"  {entry['ip']} -> {entry['mac']} ({entry['interface']})")

        print("\n=== get_lldp_neighbors ===")
        lldp = driver.get_lldp_neighbors()
        print(f"  {len(lldp)} interfaces with LLDP")

        print("\n=== get_snmp_information ===")
        snmp = driver.get_snmp_information()
        print(json.dumps(snmp, indent=2))

        print("\n=== get_users ===")
        users = driver.get_users()
        for uname, uinfo in users.items():
            print(f"  {uname}: level={uinfo['level']}")

        print("\n=== get_environment ===")
        env = driver.get_environment()
        print(json.dumps(env, indent=2))

        print("\n=== get_interfaces_counters (summary) ===")
        counters = driver.get_interfaces_counters()
        for name, info in list(counters.items())[:3]:
            print(f"  {name}: rx={info['rx_bytes']}B tx={info['tx_bytes']}B")

        print("\n=== get_route_to (10.208.1.0/24) ===")
        routes = driver.get_route_to("10.208.1.0/24")
        for route in routes:
            print(f"  {route}")

        print("\n=== get_interfaces_ip (summary) ===")
        ip_info = driver.get_interfaces_ip()
        for name, info in list(ip_info.items())[:3]:
            ipv4 = info.get('ipv4', {})
            print(f"  {name}: ipv4={ipv4}")

        print("\n✅ All tests passed!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.close()
        print("\nConnection closed.")


if __name__ == "__main__":
    test_driver()
