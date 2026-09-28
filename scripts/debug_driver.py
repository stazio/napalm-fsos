#!/usr/bin/env python3
"""Debug test of FsosDriver."""

import sys
sys.path.insert(0, "/tmp/napalm-lib")
sys.path.insert(0, "/home/staz/Programming/napalm-fsos")

from napalm_fsos.fsos import FsosDriver
import json

HOST = "10.208.1.13"
PORT = 23

driver = FsosDriver(HOST, "admin", "admin", timeout=10, optional_args={"port": PORT})

try:
    print("Opening...")
    driver.open()
    print("Connected!")

    print("\n=== get_facts ===")
    facts = driver.get_facts()
    print(json.dumps(facts, indent=2))

    print("\n=== get_vlans ===")
    vlans = driver.get_vlans()
    for vid, vinfo in vlans.items():
        print(f"  VLAN {vid}: {vinfo['name']}")

    print("\n=== get_arp_table ===")
    arp = driver.get_arp_table()
    for entry in arp:
        print(f"  {entry['ip']} -> {entry['mac']}")

    print("\n✅ Success!")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    driver.close()
